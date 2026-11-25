"""What a run puts on each host's /bkoscratch, before it starts (bs2's space preflight).

A host keeps, for every source it holds in a run, /bkoscratch/backshot2/sources/SOURCE/:
its preparation (preps/NAME: the source's shut-down overlays, its backup, dumps and logs;
bs2-host source makes one per generation, bko-holt and harness) and, only where the
source's tier 0 runs (its lead) and only for a family whose tier 0 is "chroot", its tools
(a copy of its root, bs2-host copy_tools).  An SSD case (room.py scratch(): the full vanilla3)
works on /bkoscratch too, one at a time a host (the SSD lane), and each case leaves
~5 MB of records.  On 2026-11-25 a BackShot2All start filled cinqcent's: nothing sized
any of it.

need() works out a host's peak from what is there now (bs2-host room's facts: free bytes,
and per source whether its preparation and tools are current, and their bytes) and what
will be made: a preparation's or a tools copy's bytes from SIZES (space-sizes.tsv,
measured on the hosts; a slimmed source's row is its slimmed preparation's and tools',
bs2lib/slim.py), else from the corpus dump's used bytes times the measured ratios' 90th
percentile (est.: the full guest's, the slimmed sources' rows left out of the ratios).  The
preparing goes source by source (bs2's prep_cmd), each new preparation made before the
older ones go (bs2-host prune_preps()), so the peak is a running sum, then the cases.

  estimate(source)            -> (tools bytes, prep bytes, "measured" | "est.")
  need(sources, tools, facts, ssd_peak, cases) -> Need
  check(need, free, margin)   -> None or the reason it doesn't fit
"""

import os
from dataclasses import dataclass, field

from . import sources as SRC

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES_TSV = os.path.normpath(os.path.join(HERE, "..", "space-sizes.tsv"))
GiB = 1 << 30
MARGIN_GIB = 30          # free space left over at the peak, at the least (BS2_SPACE_MARGIN_GIB)
RECORDS = 5 << 20        # a case's records on /bkoscratch (logs, dumps, report: ~5 MB)
PCTL = 0.9               # ratios(): the quantile of the measured sources' ratios a guess takes
# The boot-only filesystems a tools copy and a preparation's backup leave out (dump_used()).
BOOT = ("/boot", "/boot/efi", "/efi", "/boot/firmware")


def table(path=SIZES_TSV):
    """{source: (tools bytes, prep bytes)} of space-sizes.tsv (MiB columns; "-": none)."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                w = line.rstrip("\n").split("\t")
                if len(w) < 3 or w[0] == "source":
                    continue
                try:
                    out[w[0]] = tuple(0 if x == "-" else int(float(x) * (1 << 20)) for x in w[1:3])
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def dump_used(source):
    """The bytes SOURCE's corpus dump says its filesystems hold (used_bytes, each
    filesystem once, the boot ones left out), or None (no dump).  Alpine's three variants
    have none: alpine324's."""
    d = SRC.dump(source) or (SRC.dump("alpine324") if source in SRC.ALPINE_SOURCES else None)
    if not d:
        return None
    seen, n = set(), 0
    for m in (d.get("storage") or {}).get("mounts") or []:
        key = m.get("devno") or m.get("source")
        if m.get("alias_of") or key in seen or m.get("target") in BOOT:
            continue
        seen.add(key)
        n += int(m.get("used_bytes") or 0)
    return n


def pctl(xs, q=PCTL):
    """The Q quantile of XS (nearest rank, the higher), or None for none."""
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def ratios(path=SIZES_TSV):
    """(tools, prep) bytes per used byte of the measured sources: the PCTL quantile of
    theirs, on the safe side of most (2026-11-25: 0.74 and 0.92; above them only the
    Guix store's and Ubuntu Core's snaps, whose used bytes say little of their copies)."""
    from . import slim as SL
    t, p = [], []
    for s, (tb, pb) in table(path).items():
        if SL.slimmed(s):   # (its row is the slimmed source's: the corpus dump's used bytes are the full guest's)
            continue
        u = dump_used(s)
        if not u:
            continue
        if tb:
            t.append(tb / u)
        if pb:
            p.append(pb / u)
    return (pctl(t) or 1.0), (pctl(p) or 1.0)


def estimate(source, path=SIZES_TSV):
    """(tools bytes, prep bytes, kind): SIZES' measured row, else the corpus dump's used
    bytes times ratios() ("est."), else 20 and 5 GiB (no dump: "est.")."""
    row = table(path).get(source)
    if row:
        return row[0], row[1], "measured"
    u = dump_used(source)
    if u is None:
        return 20 * GiB, 5 * GiB, "est."
    rt, rp = ratios(path)
    return int(u * rt), int(u * rp), "est."


@dataclass
class Need:
    """A host's /bkoscratch in a run: PEAK bytes more than now at the worst moment (the
    preparing's running sum, or the end of it plus the SSD lane's case and the records),
    END the change once prepared (negative: it frees), and the words of what goes where
    (LINES: one per source that changes, biggest first)."""
    peak: int = 0
    end: int = 0
    ssd: int = 0
    records: int = 0
    lines: list = field(default_factory=list)
    est: list = field(default_factory=list)


def need(sources, tools, facts, ssd_peak=0, cases=0, path=SIZES_TSV):
    """The Need of a host preparing SOURCES (in bs2's order) with tools for TOOLS (the
    sources it leads whose family has dry runs), from FACTS (bs2-host room's "sources":
    {source: {prep_ok, prep_bytes, old_bytes, prep_referenced, tools_fresh, tools_bytes}};
    a source it lacks: nothing there yet), then its cases: SSD_PEAK bytes (its SSD lane's
    largest case) and CASES' records.  A stale current preparation goes once the new one
    is made, unless live work still refers to it (prep_referenced: prune_preps() keeps it
    while a case uses it): then it's kept, and counted on to the end."""
    cur = peak = 0
    out = Need()
    for s in sources:
        f = facts.get(s) or {}
        tb, pb, kind = estimate(s, path)
        newp = 0 if f.get("prep_ok") else pb
        kept = 0 if f.get("prep_ok") or not f.get("prep_referenced") else int(f.get("prep_bytes") or 0)
        freed = int(f.get("old_bytes") or 0) + (0 if f.get("prep_ok") or kept else int(f.get("prep_bytes") or 0))
        have_t = int(f.get("tools_bytes") or 0)
        if s in tools:
            dt = 0 if f.get("tools_fresh") else tb - have_t
        else:
            dt = -have_t
        cur += newp                     # the new preparation, made first
        peak = max(peak, cur)
        if dt < 0:                      # (tools dropped, or copied anew smaller)
            cur += dt
        else:
            cur += dt
            peak = max(peak, cur)
        cur -= freed                    # then the older preparations go
        if newp or dt or freed:
            what = []
            if newp:
                what.append(f"prep +{newp / GiB:.1f}")
            if dt:
                what.append(f"tools {'+' if dt > 0 else ''}{dt / GiB:.1f}")
            if freed:
                what.append(f"old preps -{freed / GiB:.1f}")
            if kept:
                what.append(f"stale prep kept (in use) {kept / GiB:.1f}")
            out.lines.append((newp + dt - freed, f"{s} {', '.join(what)}"))
            if kind != "measured" and (newp or dt > 0):
                out.est.append(s)
    out.ssd, out.records = int(ssd_peak), int(cases) * RECORDS
    out.end = cur
    out.peak = max(peak, cur + out.ssd + out.records)
    out.lines = [w for _, w in sorted(out.lines, key=lambda x: -abs(x[0]))]
    return out


def check(n, free, margin_gib=MARGIN_GIB):
    """None when NEED's peak leaves MARGIN_GIB free of FREE bytes; else why not, in words."""
    want = n.peak + margin_gib * GiB
    if free is None:
        return "its free space is unknown (bs2-host room didn't answer)"
    if want > free:
        return (f"needs ~{n.peak / GiB:.0f} GiB on /bkoscratch at its peak + {margin_gib:g} GiB margin = "
                f"{want / GiB:.0f} GiB, has {free / GiB:.0f} GiB free")
    return None
