#!/usr/bin/env python3
"""run-all.sh's SPLIT_CAPS from the hosts' facts (docs/design/img-speed.md F6): what each host
of a SPLIT round can run, so that its deal goes by capability, never by a host's name or class.

Usage (on the Mac):
    SPLIT_CAPS="$(testvm/test2/split-caps.py [--hosts all|H,H] [--zfs-guests "G G"])"
        --hosts       the SPLIT round's hosts (default all: every enabled amd64 host, as
                      SPLIT=all means); give run-all.sh the same SPLIT
        --zfs-guests  run-all.sh's ZFS_GUESTS, when the round sets it (default its own)

Prints one line, HOST=WORD,WORD ... for each host (the table's order), from facts_host.py
run on each at once (t2lib/facts.probe_all; nothing saved):
    zfs           caps.zfs: OpenZFS's zpool (ZFS's linux and sparse steps)
    bcachefs      caps.bcachefs: a bcachefs format tool and the kernel's file system
    netbsd-build  caps.netbsd_build: vms-bko-build/netbsd11-rust.qcow2 (sparse-rt.sh in NetBSD)
    vm:GUEST      vms/GUEST has a qcow2, for each guest of run-all.sh's guest platforms
    ceil:DISK     preflight.ram.ceiling's DISK GiB: the most one cmp-run.sh --reserve gets
Exit 1, naming it, when a host gives no facts: leave it out of --hosts and SPLIT both.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from t2lib import facts as F  # noqa: E402
from t2lib import hosts as H  # noqa: E402

# run-all.sh's guest platforms' guests (its UNITS in split_share).
PLAT_GUESTS = ("netbsd11", "alpinebsd", "freebsd15", "freebsd15-ufs", "openbsd8", "omnios58", "dragonfly6")
ZFS_GUESTS = ("ubuntu24", "ubuntu26-zfsmirror")


def words(f, guests):
    """The capability words of one host's facts F, for GUESTS."""
    c = f.get("caps") or {}
    out = [w for k, w in (("zfs", "zfs"), ("bcachefs", "bcachefs"), ("netbsd_build", "netbsd-build")) if c.get(k)]
    have = f.get("guests") or {}
    out += ["vm:" + g for g in guests if g in have]
    ceil = F.ceiling(f)
    if ceil:
        out.append(f"ceil:{ceil[0]:g}")
    return out


def line(facts, hosts, guests):
    return " ".join(f"{h}=" + ",".join(words(facts[h], guests)) for h in hosts)


def resolve(spec):
    amd = H.names(arch="amd64")
    if spec == "all":
        return list(amd)
    ns = [H.short(x) for x in spec.replace(",", " ").split()]
    bad = [n for n in ns if n not in amd]
    if bad or not ns:
        sys.exit(f"split-caps: not an enabled amd64 host: {' '.join(bad) or '(none named)'} (of {H.phrase(amd)}, or all)")
    return [n for n in amd if n in ns]


def main(argv=None):
    a = argparse.ArgumentParser(description="run-all.sh's SPLIT_CAPS from the hosts' facts")
    a.add_argument("--hosts", default="all")
    a.add_argument("--zfs-guests")
    o = a.parse_args(argv)
    hosts = resolve(o.hosts)
    guests = list(PLAT_GUESTS) + (o.zfs_guests.split() if o.zfs_guests else list(ZFS_GUESTS))
    got = F.probe_all(hosts, {"guests": guests}, save=False)
    bad = [h for h in hosts if not (got.get(h) or {}).get("ok")]
    for h in bad:
        print(f"split-caps: {h}: no facts: {(got.get(h) or {}).get('error') or 'no answer'}", file=sys.stderr)
    if bad:
        return 1
    print(line(got, hosts, guests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
