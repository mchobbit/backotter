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
whole GiB (bs2-host case_disk(), repo_shared()).  Placement (need(),
scratch()) stays the source's room: any of its cases may go to any of its hosts.
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


EPS = 1e-9
RAM, SSD = "ram", "ssd"


def scratch(source, ceiling, disk_ok, run_disk=None, path=SIZES):
    """Where SOURCE's cases work on a host, or None when the host can't hold them:
    - "ram": in the RAM disk, under cmp-run.sh --reserve D M, as every case did before
      A2: whenever need()'s D and M are within the host's CEILING ((disk, memory) GiB,
      its cmp-run.sh --ceiling; None: unknown, holds nothing);
    - "ssd": on /bkoscratch (HT_RAM=0, under --reserve-ssd D M), only for a source whose
      D is over every RAM ceiling of the run (RUN_DISK: the largest of the run's hosts'
      disk ceilings; None: this host's alone, as bs2-host on the host decides) on a host
      that takes SSD cases (DISK_OK: its holt_disk_mode is true in hosts.json, Chopin and
      Cinqcent; bs2-host runs one at a time there) whose memory ceiling holds its M:
      vanilla3's full room, D 30 GiB over the big hosts' 29 (its slimmed room, D 12, is
      in RAM: slim-sizes.tsv).
    A source one host of the run can hold in RAM never goes to the SSD on another: it
    stays in the RAM disks that hold it, as before."""
    if not ceiling:
        return None
    d, m = need(source, path)
    if m > ceiling[1] + EPS:
        return None
    if d <= ceiling[0] + EPS:
        return RAM
    top = max(ceiling[0], run_disk if run_disk is not None else ceiling[0])
    if disk_ok and d > top + EPS:
        return SSD
    return None
