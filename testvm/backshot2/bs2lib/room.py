"""A source's room: the RAM disk and memory (GiB) its cases and its
preparation reserve under cmp-run.sh --reserve.

Alpine's four sources keep bs2's own figures (case.DISK_GIB, 3 GiB of
memory: measured on BackShot2Alpine's runs, the wear log).  Every other
source takes HoltTest1's room for its guest (testvm/holttest1d/ram-sizes.tsv,
the "-" variant's D and MEM: full_of()) until bs2 measures its own: a tier 1
case is HoltTest1's four phases, with the source's boot and backup reused, so
its peak is at most HoltTest1's.  A source with no row: bs2's figures.

A slimmed source (bs2lib/slim.py: its full room is over the small hosts'
ceiling) takes its own measured room, SLIM_SIZES (testvm/backshot2/
slim-sizes.tsv: a real tier 1 case's peak on the slimmed source, measured on
the hosts), when the row's recipe is slim.RECIPE; else the full guest's, as
before.  So it lands on whichever hosts its slimmed room allows.

A source's room is its largest case's: one that backs up its own tree (content
drift, or N snapshots) into the share in its RAM disk.  A case whose backup is
the preparation's (reuse.backup_shareable()) reads that repository where it is
on /bkoscratch (ht1-bs2.sh's bs2_repo_bind), so it reserves less: shared_of(),
the slimmed source's measured d_shared, else the room less the repository's
whole GiB (bs2-host case_disk(), repo_shared()).  Should its bind fail, it copies
the repository into its RAM disk only when that provably fits (shared_peak();
ht1-bs2.sh's bs2_repo_fits), else refuses, a HARNESS row.

Placement is per case (bs2's held() and split()): a case may go to any host whose
ceiling holds its own reservation.  need() is the source's room (its own-backup
cases'); shared_need() is a shared-backup case's, the measured d_shared only (in
doubt, need()'s: an unmeasured d_shared depends on the repository's bytes on the
host, which the Mac can't know).  scratch(..., shared=True) is where such a case
works: wherever the source's own cases do, else in a RAM disk that holds
shared_need() (never the SSD lane).
"""

import os

from . import case as C

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = os.path.normpath(os.path.join(HERE, "..", "..", "holttest1d", "ram-sizes.tsv"))
SLIM_SIZES = os.path.normpath(os.path.join(HERE, "..", "slim-sizes.tsv"))
MEM = 3
KEEP = ("alpine324", "alpine324-uefi", "alpine324-lvm", "alpine324-luks")


def table(path=SIZES):
    """{guest: (D, MEM)} of ram-sizes.tsv's "-" rows (the first of each guest)."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                w = line.rstrip("\n").split("\t")
                if len(w) < 6 or w[1] != "-" or w[0] in out:
                    continue
                try:
                    out[w[0]] = (int(w[4]), int(w[5]))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def slim_table(path=None):
    """{source: row} of slim-sizes.tsv (bs2's own measured room of the slimmed sources):
    each row a dict of recipe, d, mem (GiB), t1_s (a tier 1 case's seconds, or None),
    used_before and used_after (MiB), prep_mib, tools_mib, measured (words), and d_shared
    (GiB: a case whose backup is the preparation's, read where it is; None: not measured)."""
    out = {}
    try:
        with open(path or SLIM_SIZES) as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                w = line.rstrip("\n").split("\t")
                if len(w) < 4 or w[0] == "source":
                    continue
                w += [""] * (11 - len(w))
                try:
                    row = {"recipe": int(w[1]), "d": int(w[2]), "mem": int(w[3])}
                except ValueError:
                    continue

                def num(x):
                    try:
                        return float(x)
                    except ValueError:
                        return None
                row.update(t1_s=num(w[4]), used_before=num(w[5]), used_after=num(w[6]), prep_mib=num(w[7]),
                           tools_mib=num(w[8]), measured=w[9], d_shared=int(w[10]) if w[10].strip().isdigit() else None)
                out[w[0]] = row
    except OSError:
        pass
    return out


def full_of(source, path=SIZES):
    """(disk GiB, memory GiB) of SOURCE's full guest: bs2's own figures for Alpine's four,
    else ram-sizes.tsv's (HoltTest1's), else bs2's (no row)."""
    if source in KEEP:
        return C.DISK_GIB, MEM
    return table(path).get(source, (C.DISK_GIB, MEM))


def slim_row(source, slim_path=None):
    """SOURCE's slim-sizes.tsv row when bs2 slims it and the row was measured on this
    recipe (slim.RECIPE), else None."""
    from . import slim as SL
    if not SL.slimmed(source):
        return None
    row = slim_table(slim_path).get(source)
    return row if row and row["recipe"] == SL.RECIPE else None


def of(source, path=SIZES, slim_path=None):
    """(disk GiB, memory GiB) for SOURCE's cases: a slimmed source's own measured room
    (slim_row()), else full_of()."""
    row = slim_row(source, slim_path)
    if row:
        return row["d"], row["mem"]
    return full_of(source, path)


def shared_of(source, repo_bytes, path=SIZES, slim_path=None):
    """The RAM disk (GiB) a case of SOURCE reserves whose backup is its preparation's,
    read where it is (its repository, REPO_BYTES, not in the RAM disk): a slimmed
    source's measured d_shared (slim_row()), else of()'s D less the repository's whole
    GiB, never under case.DISK_GIB.  (Every D is a case's with its repository in its
    share, ceil(1.10 x peak + 0.25): less the repository's whole GiB it is still at least
    1.10 x the peak without them + 0.25.)"""
    row = slim_row(source, slim_path)
    if row and row.get("d_shared"):
        return max(C.DISK_GIB, row["d_shared"])
    return max(C.DISK_GIB, of(source, path, slim_path)[0] - int(max(0, repo_bytes) // (1 << 30)))


def shared_peak(source, repo_bytes, path=SIZES, slim_path=None):
    """The most (bytes) a case of SOURCE whose backup is its preparation's holds in its RAM
    disk with that repository (REPO_BYTES) not there, as far as its measured room tells:
    the largest peak its room's rule allows, D = ceil(1.10 x peak + 0.25) GiB, so a
    measured d_shared's (d_shared - 0.25) / 1.10 GiB; a slimmed source with no d_shared,
    its d's the same way less the repository; any other, its room itself (ram-sizes.tsv's
    D, another rule) less the repository; never under 0.  ht1-bs2.sh's copy fallback
    (bs2_repo_fits, BS2_REPO_PEAK_KIB) copies the repository into such a case's RAM disk
    only when its reservation holds this and the copy."""
    G = 1 << 30
    repo = max(0, repo_bytes)
    row = slim_row(source, slim_path)
    if row and row.get("d_shared"):
        return max(0, int((row["d_shared"] - 0.25) / 1.10 * G))
    if row:
        return max(0, int((row["d"] - 0.25) / 1.10 * G) - repo)
    return max(0, full_of(source, path)[0] * G - repo)


def need(source, path=SIZES):
    """The least (disk GiB, memory GiB) any of SOURCE's reservations asks: a
    queue case's (bs2-host case_disk and case_mem: bs2's own 2 and 3, or the
    source's room if more; a case's disk grows from there for the inode tables
    it writes, but never past the host's ceiling), which covers its
    preparation's too (2, and the same memory).  A host whose cmp-run.sh
    --ceiling is under either can never hold the source's cases: cmp-run.sh
    refuses each at once (bs2's held() leaves such a host's copy out)."""
    d, m = of(source, path)
    return max(C.DISK_GIB, d), max(MEM, m)


def shared_need(source, path=SIZES, slim_path=None):
    """The least (disk GiB, memory GiB) a shared-backup case of SOURCE reserves, for
    placement: its measured d_shared (shared_of()'s first rule, at least case.DISK_GIB)
    with need()'s memory; need() itself when d_shared isn't measured (shared_of()'s other
    rule takes the repository's bytes on the host: in doubt, the full room)."""
    d, m = need(source, path)
    row = slim_row(source, slim_path)
    if row and row.get("d_shared"):
        return min(d, max(C.DISK_GIB, row["d_shared"])), m
    return d, m


EPS = 1e-9
RAM, SSD = "ram", "ssd"


def scratch(source, ceiling, disk_ok, path=SIZES, shared=False):
    """Where SOURCE's cases work on a host, or None when the host can't hold them:
    - "ram": in the RAM disk, under cmp-run.sh --reserve D M, as every case did before
      A2: whenever need()'s D and M are within the host's CEILING ((disk, memory) GiB,
      its cmp-run.sh --ceiling; None: unknown, holds nothing);
    - "ssd": on /bkoscratch (HT_RAM=0, under --reserve-ssd D M), for a source whose D is
      over this host's RAM-disk ceiling on a host that takes SSD cases (DISK_OK:
      hosts.py disk_scratch(), its scratch_mode overflow or its holt_disk_mode; every
      amd64 host since img-speed F3, 2026-11-27; bs2-host runs one at a time there)
      whose memory ceiling holds its M: the large desktop sources (D 10-20) on the
      small hosts' 9.0, vanilla3's full room (D 30) on the big hosts' 29.  Before F3 the
      SSD was the big hosts' alone (holt_disk_mode), and only for a source no RAM disk of
      the run could hold; now placement (bs2 split()) weighs each host's one-at-a-time
      SSD lane against the others' RAM slots.
    SHARED: where a shared-backup case of SOURCE works (bs2-host repo_shared()): where its
    own cases do, else "ram" when shared_need() is within CEILING (a host that holds such
    cases only: bs2's SHARED_ONLY, which only a host with no SSD cases has), else None."""
    if not ceiling:
        return None
    d, m = need(source, path)
    if m > ceiling[1] + EPS:
        return None
    if d <= ceiling[0] + EPS:
        return RAM
    if disk_ok:
        return SSD
    if shared and shared_need(source, path)[0] <= ceiling[0] + EPS:
        return RAM
    return None


# ---- how many of a source's cases are shared-backup ones (bs2's per-case placement) ----

SHARE_SAMPLE = 200       # shared_share(): the seeds it generates
SHARE_FROM = 100000      # their first: bs2-host enqueue's tier 1 draws start there
_SHARES = {}


def shared_share(source, sample=SHARE_SAMPLE, start=SHARE_FROM):
    """The share of SOURCE's tier 1 cases whose backup is the preparation's tree
    (reuse.backup_shareable(), bs2-host case_shared(): repo_shared()'s own half), from the
    generator (generate.generate()) on SAMPLE seeds from START over its corpus dump (the
    drawn ones are the host's dump's, a slimmed source's slimmed: the same draws of content
    drift and snapshots, other edits).  0 when it can't be worked out (no dump, an error):
    then every case is placed by the source's full room.  Cached a source."""
    key = (source, sample, start)
    if key in _SHARES:
        return _SHARES[key]
    p = 0.0
    try:
        from . import generate as G, reuse as RU, sources as SRC
        d = SRC.dump(source)
        if d:
            k = sum(1 for seed in range(start, start + sample)
                    if RU.backup_shareable(G.generate(source, None, d, seed, caps={}))[0])
            p = k / sample
    except Exception:   # (an estimate: in doubt, the full room)
        p = 0.0
    _SHARES[key] = p
    return p


def shared_slots(n, p):
    """Of N cases, each a shared-backup one with chance P, how many to plan as such: a low
    estimate, the mean less a standard deviation (rounded down, at least 0), so the drawn
    ones fill those slots most often (bs2 deal_source(): a slot no such case fills is left
    empty, a case no own-backup slot takes goes to its source's lead)."""
    import math
    if n <= 0 or p <= 0:
        return 0
    return max(0, int(math.floor(n * p - math.sqrt(n * p * (1 - p)))))
