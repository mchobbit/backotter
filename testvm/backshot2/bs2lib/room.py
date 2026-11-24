"""A source's room: the RAM disk and memory (GiB) its cases and its
preparation reserve under cmp-run.sh --reserve.

Alpine's four sources keep bs2's own figures (case.DISK_GIB, 3 GiB of
memory: measured on BackShot2Alpine's runs, the wear log).  Every other
source takes HoltTest1's room for its guest (testvm/holttest1d/ram-sizes.tsv,
the "-" variant's D and MEM) until bs2 measures its own: a tier 1 case is
HoltTest1's four phases, with the source's boot and backup reused, so its
peak is at most HoltTest1's.  A source with no row: bs2's figures.
"""

import os

from . import case as C

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = os.path.normpath(os.path.join(HERE, "..", "..", "holttest1d", "ram-sizes.tsv"))
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


def of(source, path=SIZES):
    """(disk GiB, memory GiB) for SOURCE's cases."""
    if source in KEEP:
        return C.DISK_GIB, MEM
    return table(path).get(source, (C.DISK_GIB, MEM))


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
      vanilla3, D 30 GiB over the big hosts' 29, today.
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
