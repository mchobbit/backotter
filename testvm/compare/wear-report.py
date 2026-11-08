#!/usr/bin/env python3
"""What the test hosts' SSDs have written: per job (iolog.sh's cgroup
counts) by wear tag, and per disk per day (SMART), from the wear logs
wear-setup.sh set up.

Usage: wear-report.py [--since YYYY-MM-DD] [--top N] [--depth N] [--tag PREFIX] [DIR ...]
DIR defaults to /bkoscratch/wear; give one per host (e.g. copies fetched
from each) for both at once.

The groups are each host's jobs by wear tag (WEAR_TAG, FAMILY:RUN:CELL,
e.g. ht1:r4all:pop24), cut to its first --depth parts (default 2, family
and run; 0: the whole tag).  --tag PREFIX keeps the jobs whose tag starts
with those parts (ht1, or ht1:r4all).  A job logged without a tag (every
line from before tags, and any caller that sets none) gets one guessed
from its WHAT, marked ~: "reserve sudo -E ... ht1-bsd.sh omnios58 raid4"
is ~ht1:raid4:omnios58, a queue job "u3 mx25" ~cmp:u3:mx25, an empty WHAT
~?.  Only physical disks count as written: zram is swap in RAM, shown
apart, and nbd, loop, md, dm and zd (ZFS volume) devices are what the
writes went through or into (a qemu-nbd scope's line counts as its job's).
Swap per group: the zram its jobs wrote, the most swap one job held (zram
and disk), and in how many jobs the host wrote to disk swap (everyone's:
never summed).

The end has the wear line of each host's disks per day: SMART's figure,
what logged jobs wrote to the disk (all of them, whatever --tag says),
and the rest, written outside them.
"""
import argparse
import bisect
import collections
import re
import sys
from pathlib import Path

GB = 1e9
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
VIRTUAL = re.compile(r"(zram|loop|nbd|md|ram|sr|zd)\d+|dm-\d+")
EXTRA = {"tag", "serial", "swpeak", "mpeak", "host_dswap", "host_zram"}
# A --reserve job's WHAT is "reserve CMD...": its wrappers, options, numbers
# and VAR=VALUE words are skipped, and the command's name gives the family.
WRAP = {"sudo", "env", "timeout", "nice", "ionice", "setsid", "setpriv", "bash", "sh", "python3", "stdbuf", "exec"}
SKIP = re.compile(r"-.*|\d+(\.\d+)?[smhd]?")
VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
FAMILY = {"ht1.sh": "ht1", "ht1-bsd.sh": "ht1", "ht1-offline.sh": "ht1", "check.sh": "stic",
          "build-holt.sh": "build:holt", "img-one.sh": "img", "rebuild-one.sh": "rebuild",
          "run-guest-rt.sh": "rt", "audit-host.sh": "audit", "compare.py": "cmp", "vm-update": "vm-update"}


def gb(b: float) -> str:
    return f"{b / GB:,.1f} GB"


def mb(b: float) -> str:   # swap: small, and any of it on disk matters
    return gb(b) if b >= 0.1 * GB else f"{b / 1e6:,.1f} MB" if b >= 1e5 else f"{b:,.0f} B"


def num(s):
    """A count, or None."""
    return int(s) if s and s.isascii() and s.isdigit() else None


def clean(serial: str) -> str:   # as iolog.sh writes serials
    return re.sub(r"[^A-Za-z0-9._-]", "", serial)


def guessed_tag(what: str) -> str:
    w = what.split()
    if not w:
        return "~?"
    if what.startswith("nbd for "):     # a qemu-nbd scope whose job left no line
        m = re.fullmatch(r"bko-(.+)-\d+-\d+\.scope", w[-1])
        return "~cmp:" + m.group(1) if m else "~nbd"
    if w[0] != "reserve":       # a queue job: "TAG GUEST"
        return "~cmp:" + ":".join(w[:2]) if len(w) > 1 else "~" + w[0]
    i = 1
    while i < len(w):
        if SKIP.fullmatch(w[i]) or VAR.fullmatch(w[i]) or w[i].rsplit("/", 1)[-1] in WRAP:
            i += 1
        elif w[i] == "cd":          # bash -c 'cd DIR && CMD' or 'cd DIR; CMD': CMD
            i += 2
            if i < len(w) and w[i] in ("&&", ";"):
                i += 1
        else:
            break
    if i >= len(w):
        return "~reserve"
    env = dict(a.split("=", 1) for a in w[1:i] if VAR.fullmatch(a))
    cmd, args = w[i], [a for a in w[i + 1:] if not a.startswith("-")]
    fam = FAMILY.get(cmd.rsplit("/", 1)[-1], cmd.rsplit("/", 1)[-1])
    if fam == "ht1" and len(args) >= 2:      # ht1*.sh GUEST RUN [PHASE...]
        v = env.get("HT_VARIANT")
        return f"~ht1:{args[1]}:{args[0]}" + (f"-{v}" if v else "")
    if fam == "stic" and "/" in cmd:         # stictest1/GUEST/check.sh ... MARK-GUEST ...
        g = cmd.rsplit("/", 2)[-2]
        mark = next((a[:-len(g) - 1] for a in args if a.endswith("-" + g) and len(a) > len(g) + 1), "?")
        return f"~stic:{mark}:{g}"
    if fam in ("build:holt", "img", "rebuild", "rt") and args:
        return f"~{fam}:{args[0]}"
    return "~" + fam


def under(tag: str, prefix: str) -> bool:
    """TAG (~ aside) starts with PREFIX's parts: ht1 takes ht1:r4all:pop24, not ht1dbg:pop24."""
    p = prefix.lstrip("~").rstrip(":").split(":")
    return tag.lstrip("~").split(":")[:len(p)] == p


def read_smart(dirs):
    """(host, serial) -> [(utc, bytes, pct, model, disk)], and (host, disk) -> ([utc], [serial])."""
    rows, seen = collections.defaultdict(list), collections.defaultdict(list)
    for d in dirs:
        p = Path(d) / "smart.log"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8", errors="replace").split("\n"):
            f = ln.split("\t")
            if len(f) < 9 or not STAMP.fullmatch(f[0]) or num(f[5]) is None:
                continue
            serial = (clean(f[4]) if f[4] != "-" else "") or f[2]   # none: the disk's name
            rows[(f[1], serial)].append((f[0], num(f[5]), f[6], f[3], f[2]))
            seen[(f[1], f[2])].append((f[0], serial))
    for v in rows.values():
        v.sort()
    names = {}
    for k, v in seen.items():
        v.sort()
        names[k] = ([t for t, _ in v], [s for _, s in v])
    return rows, names


def serial_of(host, utc, disk, x, names):
    """A job line's DISK as SMART's serial: the line's own serial= field,
    else the one SMART saw under that name at or before the line (the first
    after it, for a line from before any reading)."""
    for kv in x.get("serial", "").split(","):
        k, _, v = kv.partition(":")
        if k == disk and clean(v):
            return clean(v)
    if (host, disk) not in names:
        return disk
    times, serials = names[(host, disk)]
    i = bisect.bisect_right(times, utc)
    return serials[i - 1] if i else serials[0]


def read_jobs(dirs):
    """[(fields, extras, disks)], one per job, its qemu-nbd scopes' disks added."""
    lines, nbd, nbd_line = [], collections.defaultdict(collections.Counter), {}
    for d in dirs:
        p = Path(d) / "jobs.log"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8", errors="replace").split("\n"):
            f = ln.split("\t")
            # (Not a line: the rest of an old WHAT that had a newline, from
            # before iolog.sh made them spaces.)
            if len(f) < 7 or not STAMP.fullmatch(f[0]):
                continue
            x = {k: v for k, _, v in (kv.partition("=") for kv in f[7:]) if k in EXTRA}
            disks = collections.Counter()
            if f[5] != "-":
                for kv in f[5].split(","):
                    k, _, v = kv.partition("=")
                    if k and num(v) is not None:
                        disks[k] += num(v)
            if f[6].startswith("nbd for "):
                key = (f[1], f[6][len("nbd for "):])
                nbd[key] += disks
                nbd_line[key] = (f, x)
            else:
                lines.append((f, x, disks))
    # A qemu-nbd scope whose job left no line (killed with SIGKILL) counts by itself.
    seen = {(f[1], f[2]) for f, _, _ in lines}
    lines += [(f, x, nbd.pop(k)) for k, (f, x) in nbd_line.items() if k not in seen]
    return [(f, x, disks + nbd.get((f[1], f[2]), collections.Counter())) for f, x, disks in lines]


def jobs(lines, since, top, depth, prefix, names):
    """Prints the groups and the top jobs; returns (host, serial) -> [(utc, bytes)], every job's."""
    per_disk = collections.defaultdict(list)
    # group -> [jobs, SSD bytes, seconds, zram bytes, most swap held, jobs with host disk swap, most of it]
    by_group = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0])
    each = []
    for f, x, disks in lines:
        ssd = {k: v for k, v in disks.items() if not VIRTUAL.fullmatch(k)}
        for k, v in ssd.items():
            per_disk[(f[1], serial_of(f[1], f[0], k, x, names))].append((f[0], v))
        tag = x.get("tag") or guessed_tag(f[6])
        if (since and f[0][:10] < since) or (prefix and not under(tag, prefix)):
            continue
        total = sum(ssd.values())
        group = ":".join(tag.split(":")[:depth]) if depth else tag
        g = by_group[(f[1], group)]
        g[0] += 1
        g[1] += total
        g[2] += num(f[4]) or 0
        g[3] += sum(v for k, v in disks.items() if k.startswith("zram"))
        g[4] = max(g[4], num(x.get("swpeak")) or 0)
        hd = num(x.get("host_dswap")) or 0
        g[5] += hd > 0
        g[6] = max(g[6], hd)
        each.append((total, f[0], f[1], tag, f[6], ssd))
    if not lines:
        print("No jobs logged (jobs.log) yet.")
    elif not each:
        print("No jobs logged" + (f" with a tag under {prefix}" if prefix else "")
              + (f" since {since}" if since else "") + ".")
    else:
        print("SSD writes per group of jobs (host, group: jobs, written, per job, hours; then the zram they wrote,"
              " the most swap one job held, and the jobs during which the host wrote to disk swap):")
        for (host, group), (n, b, s, z, sp, nd, md) in sorted(by_group.items(), key=lambda kv: (-kv[1][1], kv[0])):
            swap = f"; zram {mb(z)}" if z else ""
            swap += f"; swap held {mb(sp)}" if sp else ""
            swap += f"; host disk swap in {nd} jobs (up to {mb(md)})" if nd else ""
            print(f"  {host} {group}: {n} jobs, {gb(b)}, {gb(b / n)} each, {s / 3600:.1f} h{swap}")
        if top > 0:
            print(f"The {top} jobs that wrote most:")
            for total, utc, host, tag, what, ssd in sorted(each, key=lambda e: e[:3], reverse=True)[:top]:
                disks = ", ".join(f"{k} {gb(v)}" for k, v in ssd.items()) or "no disk"
                print(f"  {gb(total):>10}  {host} {utc} {tag}  {' '.join(what.split())[:120]}  ({disks})")
    return per_disk


def wear_lines(rows, since, per_disk):
    if not rows:
        print("No SMART readings (smart.log) yet.")
        return
    print("Wear lines, per disk per day (SMART, from the last reading of the day before to the day's last;"
          " the first day from the first reading): written, of that in logged jobs, and the rest")
    for (host, serial), r in sorted(rows.items()):
        utc, life, pct, model, disk = r[-1]
        print(f"  {host} {disk} {model} ({serial}): {gb(life)} in its life, {pct}% used")
        done = sorted(per_disk.get((host, serial), ()))   # (end utc, bytes) of the jobs on this disk
        times = [t for t, _ in done]
        last = {}
        for t, b, *_ in r:
            last[t[:10]] = (t, b)
        t0, b0 = r[0][0], r[0][1]
        for day in sorted(last):
            t, b = last[day]
            j = sum(v for _, v in done[bisect.bisect_right(times, t0):bisect.bisect_right(times, t)])
            if not since or day >= since:
                print(f"    {day}: {gb(b - b0)}, jobs {gb(j)}, rest {gb(b - b0 - j)}")
            t0, b0 = t, b
        j = sum(v for _, v in done[bisect.bisect_right(times, t0):])
        if j >= 0.05 * GB:
            print(f"    after its last reading ({t0}): jobs {gb(j)}")


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--since", help="only jobs and days from YYYY-MM-DD on")
    a.add_argument("--top", type=int, default=10, help="list the N jobs that wrote most (default 10)")
    a.add_argument("--depth", type=int, default=2, help="tag parts to group by (default 2; 0: the whole tag)")
    a.add_argument("--tag", help="only jobs whose tag starts with these parts (ht1, ht1:r4all), guessed ~ tags too")
    a.add_argument("dirs", nargs="*", default=["/bkoscratch/wear"])
    o = a.parse_args()
    if o.depth < 0:
        a.error("--depth: 0 or more")
    rows, names = read_smart(o.dirs)
    per_disk = jobs(read_jobs(o.dirs), o.since, o.top, o.depth, o.tag, names)
    print()
    wear_lines(rows, o.since, per_disk)


if __name__ == "__main__":
    sys.exit(main())
