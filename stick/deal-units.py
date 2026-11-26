#!/usr/bin/env python3
"""Deal a round's build units over the build hosts, for stick/build-round.sh.

A round is a fixed set of build units: restic-bko (one Go run, every OS at
once), rustic-bko for Linux, for DragonFly (a cross build) and for Haiku, the
four native guest builds (freebsd, netbsd, openbsd, illumos), and with --arch
arm64 the two arm64 units that an amd64 host builds (restic-bko linux-arm64
and the cross-built check of the arm64 host's rustic-bko) plus the check of
restic-bko linux-arm64.  Each unit says what it needs of a host (Go; the
rustup toolchain and a target; DragonFly's pinned nightly and its sysroot
image; a vms-bko-build overlay for its guest; haiku6; zig and cargo-zigbuild)
and about how long it takes, measured over the rounds:

    unit                 minutes  what was measured
    restic                     4  Go cross-builds the six OSes in one run
    linux                     15  the longest: fat LTO, one codegen unit
    dragonfly                 10  the cross build, fat LTO
    haiku                     25  native in haiku6, by far the slowest
                                  (its own watchdog allows 5400 s)
    freebsd                    7  native in freebsd14
    netbsd                     6  native in netbsd11
    openbsd                   12  native in openbsd8, the slowest BSD (11.5)
    illumos                    6  native in omnios58
    restic-arm64               2  one Go target
    restic-arm64-check         2  the same, on another host
    linux-arm64-check         15  the cross build through cargo-zigbuild

The minutes are estimates, used only to balance the deal; nothing fails on
them.  Haiku's is the least certain (its backstop timeout is 9000 s).

The rule.  Hosts are given in the host table's order, each with its class
(big, small) and the capabilities it has.  A class says how many units a host
runs at once: a big host three, two of them native guest builds; a small host
two, one of them a native guest build (the user's choice, 2026-11-14: a small
host has 16 threads, 27 GiB and a 10G RAM disk).  A host's estimated wall time
is therefore max(cpu minutes / cpu lanes, guest minutes / guest lanes).  Units
are dealt longest first (ties by name), each to the host whose estimated wall
time ends up smallest, and among those to the first in the table's order; a
host that doesn't meet the unit's requirement is never considered.  A check
unit is dealt after the unit it checks and never to that unit's host: a check
build is worth having only when another machine made it.  A check that no
other host can take is left out and said (as a round built on one host has
always left it out), linux-arm64-check too, whose unit the arm64 host builds;
a shipped unit that no host can take is an error.

With --legacy (build-round.sh's --split legacy) the deal is the fixed two-way
split the rounds used before 2026-11-14 (Chopin: restic, linux, dragonfly,
haiku; Cinqcent: the four native builds; with arm64, Chopin's restic-arm64 and
linux-arm64-check and Cinqcent's restic-arm64-check), so that an older round
can be reproduced as it was built.  --legacy takes only those two hosts, and no
capabilities; without it every host given is dealt units, however many there
are and whichever they are.

Usage:
    deal-units.py deal  [--arch LIST] [--only LIST] [--legacy] HOST=CLASS=CAPS...
    deal-units.py table [--arch LIST] [--only LIST] [--legacy] HOST=CLASS=CAPS...
    deal-units.py units [--arch LIST]

HOST=CLASS=CAPS: a build host, its class, and its capabilities separated by
commas (empty: none; --legacy ignores both).  --arch: amd64, arm64 or
amd64,arm64 (default amd64).  --only: components to build, comma-separated, of
restic linux dragonfly haiku freebsd netbsd openbsd illumos (default all).

deal prints a line per host, "HOST<TAB>UNIT,UNIT,..." in the order given (a
host that got nothing: an empty field), then a "skipped<TAB>UNIT<TAB>WHY" line
for each check left out.  table prints what --dry-run shows.  units prints the
unit table, "NAME<TAB>COMPONENT<TAB>MINUTES<TAB>KIND<TAB>NEEDS<TAB>CHECKS".

Exit 0; 2 on bad usage, and when a shipped unit has no host that meets its
requirement (the reason names every host and what it lacks).

Image units (stick/img-round.sh plan, docs/design/img-speed.md §5.2 step 3).  A platform the
plan builds is one unit: img-one.sh's release and test builds and its boot tests, under one
img-lanes.sh reservation.  Measured minutes (§1.2, the second set of 2026-11-26 on Chopin,
compression left out) and est. 4 for a refresh (§5.2: 3-5):

    platform   full  refresh      platform   full  refresh
    linux       8.5    4          openbsd     5.0    4
    freebsd    10.1    4          illumos    16.8    4
    dragonfly   8.2    4          haiku       3.0    4
    netbsd      3.8    4

A big host runs two image units at once, a small host one (memory 10 of 10.1; D4), and no
host more than one FreeBSD unit (its builder's port 2400).  A host's estimated finish is its
units' minutes over its lanes (never less than its longest), times 1 + its load a core as
pick-host rates it (Chopin's load counts 4 higher: pick-host's LOAD_PENALTY), plus 5 minutes
when a unit's reservation doesn't fit its room now (a small host's 10 GiB waits for all of
its memory).  Units go longest first (ties by platform) to the host that would then finish
earliest, ties to the first in the order given (img_round.py gives hosts.py's tie order); a
unit only to the hosts the plan found able (its inputs, C7; its room's ceiling; for a
refresh, the parent).  A unit no host can take is left out and said: the plan names it.

    deal-units.py img-units            NAME<TAB>FULL<TAB>REFRESH per platform
    deal-units.py img-deal < JSON      {"hosts": [{name, class, load_per_core, fits_now: {P: bool}}],
                                        "units": [{platform, action, able: [host]}]} -> the deal as JSON
"""

import collections
import sys

# A build unit: its name, the component --only names, its estimated minutes,
# its kind (cpu, or guest: it boots a test guest and takes one of the host's
# guest slots), the capabilities the host must have, the unit this one checks
# (which must be built on another host; empty: none), and its architecture.
Unit = collections.namedtuple("Unit", "name component minutes kind needs checks arch")

UNITS = tuple(Unit(*u) for u in (
    ("restic", "restic", 4, "cpu", ("go",), "", "amd64"),
    ("linux", "linux", 15, "cpu", ("musl",), "", "amd64"),
    ("dragonfly", "dragonfly", 10, "cpu", ("dragonfly",), "", "amd64"),
    ("haiku", "haiku", 25, "guest", ("haiku",), "", "amd64"),
    ("freebsd", "freebsd", 7, "guest", ("vm-freebsd",), "", "amd64"),
    ("netbsd", "netbsd", 6, "guest", ("vm-netbsd",), "", "amd64"),
    ("openbsd", "openbsd", 12, "guest", ("vm-openbsd",), "", "amd64"),
    ("illumos", "illumos", 6, "guest", ("vm-illumos",), "", "amd64"),
    ("restic-arm64", "restic", 2, "cpu", ("go",), "", "arm64"),
    ("restic-arm64-check", "restic", 2, "cpu", ("go",), "restic-arm64", "arm64"),
    # The check of the arm64 host's native rustic-bko (linux-arm64): a cross
    # build here.  The unit it checks is the arm64 host's own, which no build
    # host is dealt, so the "never the host that builds it" rule never bites;
    # naming it says this is a check, which is left out when no host can take
    # it rather than refusing the round (build-round.sh's xcheck says so).
    ("linux-arm64-check", "linux", 15, "cpu", ("zig", "arm64-musl"), "linux-arm64", "arm64"),
))

COMPONENTS = ("restic", "linux", "dragonfly", "haiku", "freebsd", "netbsd", "openbsd", "illumos")
ARCHES = ("amd64", "arm64")

# A class's lanes: how many units at once, and how many of those may be native
# guest builds.  A class not named here gets the small one's.
LANES = {"big": (3, 2), "small": (2, 1)}

# The split the rounds used before four build hosts (--legacy).
LEGACY = {"chopin": ("restic", "linux", "dragonfly", "haiku", "restic-arm64", "linux-arm64-check"),
          "cinqcent": ("freebsd", "netbsd", "openbsd", "illumos", "restic-arm64-check")}

# What a missing capability means, for the messages.
WHY = {"go": "no Go", "musl": "no Rust x86_64-unknown-linux-musl",
       "dragonfly": "no DragonFly nightly or sysroot image", "haiku": "no haiku6 image",
       "zig": "no zig and cargo-zigbuild (stick/zig-tools.sh --install)",
       "arm64-musl": "no Rust aarch64-unknown-linux-musl"}


class DealError(Exception):
    """Bad usage, or a host list the deal can't work with."""


def why_not(caps, needs):
    """Why a host with CAPS can't take a unit that NEEDS these, or ""."""
    miss = [n for n in needs if n not in caps]
    return ", ".join(WHY.get(n, "no " + n) for n in miss)


def lanes_of(cls):
    """A class's (cpu lanes, guest lanes)."""
    return LANES.get(cls, LANES["small"])


def units_for(arch=("amd64",), only=None):
    """The units of these architectures whose component --only names, in UNITS' order."""
    return [u for u in UNITS if u.arch in arch and (only is None or u.component in only)]


class Host:
    """A build host in the deal: its name, class, capabilities and what it has been dealt."""

    def __init__(self, name, cls, caps):
        self.name, self.cls, self.caps = name, cls, set(caps)
        self.cpu_lanes, self.guest_lanes = lanes_of(cls)
        self.units = []

    def minutes(self, extra=None):
        """This host's estimated wall time with EXTRA dealt to it as well: its cpu and guest
        units each spread over their lanes, the two kinds running side by side, and never
        less than its longest single unit (one unit in two lanes is still one unit)."""
        got = self.units + ([extra] if extra else [])
        out = 0.0
        for kind, lanes in (("cpu", self.cpu_lanes), ("guest", self.guest_lanes)):
            mine = [u.minutes for u in got if u.kind == kind]
            if mine:
                out = max(out, sum(mine) / float(lanes), max(mine))
        return out

    def names(self):
        return [u.name for u in self.units]


def deal(hosts, arch=("amd64",), only=None, legacy=False):
    """Deal the units over HOSTS (Host objects, in the table's order).  Returns
    (hosts, skipped), skipped a list of (unit name, why) for the checks left out.
    A DealError names a shipped unit no host can build."""
    want = units_for(arch, only)
    if not hosts:
        raise DealError("no build host to deal the round's units over")
    if legacy:
        for h in hosts:
            if h.name not in LEGACY:
                raise DealError(f"--legacy is the split of chopin and cinqcent alone, not {h.name}")
            h.units = [u for u in want if u.name in LEGACY[h.name]]
        # What the other host of the split would have built (--hosts chopin alone builds no
        # native guest build, as it never has) is left out and said, check or not.
        mine = {u.name for h in hosts for u in h.units}
        skipped = []
        for u in want:
            if u.name in mine:
                continue
            owner = next(n for n, us in LEGACY.items() if u.name in us)
            skipped.append((u.name, f"the legacy split builds it on {owner}, which --hosts doesn't name"))
        return hosts, skipped
    skipped = []
    # Longest first, ties by name; the units a check checks before the checks,
    # so that a check knows which host to keep off.
    order = sorted(want, key=lambda u: (bool(u.checks), -u.minutes, u.name))
    for u in order:
        keep_off = ""
        if u.checks:
            keep_off = next((h.name for h in hosts if u.checks in h.names()), "")
        able, refused = [], []
        for h in hosts:
            bad = why_not(h.caps, u.needs)
            if bad:
                refused.append(f"{h.name}: {bad}")
            elif h.name == keep_off:
                refused.append(f"{h.name}: it builds {u.checks} itself")
            else:
                able.append(h)
        if not able:
            why = "; ".join(refused)
            if u.checks:
                skipped.append((u.name, f"no other host can build it ({why})"))
                continue
            raise DealError(f"no build host can build {u.name}: {why}")
        best = min(able, key=lambda h: (h.minutes(u), hosts.index(h)))
        best.units.append(u)
    for h in hosts:
        h.units.sort(key=lambda u: UNITS.index(u))
    return hosts, skipped


def table(hosts, skipped):
    """The deal as the lines --dry-run prints."""
    w = max([len(h.name) for h in hosts] + [4])
    out = ["The round's build units, dealt over the build hosts (estimated minutes):",
           f"  {'HOST'.ljust(w)}  CLASS  LANES  EST  UNITS"]
    for h in hosts:
        us = ", ".join(f"{u.name} {u.minutes}" for u in h.units) or "-"
        out.append(f"  {h.name.ljust(w)}  {h.cls.ljust(5)}  {h.cpu_lanes}/{h.guest_lanes}"
                   f"    {int(round(h.minutes())):3d}  {us}")
    for name, why in skipped:
        out.append(f"  not built: {name}: {why}")
    return out


def parse_host(arg):
    """HOST=CLASS=CAPS -> a Host."""
    parts = arg.split("=")
    if len(parts) != 3 or not parts[0]:
        raise DealError(f"a host is NAME=CLASS=CAPS (commas between the capabilities), not '{arg}'")
    name, cls, caps = parts
    return Host(name, cls or "small", [c for c in caps.split(",") if c])


# --- Image units (img-round.sh plan) ------------------------------------------------------------

# platform: (full minutes, refresh minutes).  Full: §1.2's release build + test build + boot
# tests; refresh: est. (§5.2).
IMG_MINUTES = {"linux": (8.5, 4.0), "freebsd": (10.1, 4.0), "dragonfly": (8.2, 4.0), "netbsd": (3.8, 4.0),
               "openbsd": (5.0, 4.0), "illumos": (16.8, 4.0), "haiku": (3.0, 4.0)}
IMG_ACTIONS = ("full", "refresh")
IMG_LANES = {"big": 2, "small": 1}
# The minutes a unit is taken to wait when its reservation doesn't fit the host's room now.
IMG_WAIT_MIN = 5.0
# At most one unit of these per host: FreeBSD's builder listens on port 2400.
IMG_ONE_PER_HOST = ("freebsd",)


def img_minutes(platform, action):
    if platform not in IMG_MINUTES or action not in IMG_ACTIONS:
        raise DealError(f"no image unit {platform} {action}")
    return IMG_MINUTES[platform][IMG_ACTIONS.index(action)]


class ImgHost:
    """A build host in an image deal: its class's lanes, its load a core (pick-host's, with
    Chopin's penalty), which platforms' reservations fit its room now, and its units."""

    def __init__(self, name, cls="small", load_per_core=0.0, fits_now=None):
        self.name, self.cls = name, cls or "small"
        self.lanes = IMG_LANES.get(self.cls, IMG_LANES["small"])
        lpc = float(load_per_core) if load_per_core is not None else 0.0
        self.factor = 1.0 + (lpc if lpc != float("inf") else 1.0)
        self.fits_now = dict(fits_now or {})
        self.units = []   # [(platform, action, minutes)]

    def minutes(self, extra=None):
        got = self.units + ([extra] if extra else [])
        if not got:
            return 0.0
        mins = [u[2] for u in got]
        out = max(sum(mins) / float(self.lanes), max(mins)) * self.factor
        if any(not self.fits_now.get(u[0], True) for u in got):
            out += IMG_WAIT_MIN
        return out


def deal_images(hosts, units):
    """Deal image UNITS ([{platform, action, able}]) over HOSTS (ImgHost, in tie order).
    Returns (hosts, placed {platform: host}, left [(platform, why)])."""
    order = sorted(units, key=lambda u: (-img_minutes(u["platform"], u["action"]), u["platform"]))
    placed, left = {}, []
    for u in order:
        p, a = u["platform"], u["action"]
        m = img_minutes(p, a)
        able = [h for h in hosts if h.name in (u.get("able") or ())
                and not (p in IMG_ONE_PER_HOST and any(x[0] == p for x in h.units))]
        if not able:
            left.append((p, "no able host" if not u.get("able") else "its able hosts are full"))
            continue
        best = min(able, key=lambda h: (h.minutes((p, a, m)), hosts.index(h)))
        best.units.append((p, a, m))
        placed[p] = best.name
    return hosts, placed, left


def img_deal_json(doc):
    hosts = [ImgHost(h["name"], h.get("class"), h.get("load_per_core"), h.get("fits_now")) for h in doc.get("hosts") or ()]
    hosts, placed, left = deal_images(hosts, doc.get("units") or ())
    return {"placed": placed, "left": [list(x) for x in left],
            "hosts": [{"name": h.name, "class": h.cls, "lanes": h.lanes, "est_min": round(h.minutes(), 1),
                       "units": [{"platform": u[0], "action": u[1], "minutes": u[2]} for u in h.units]} for h in hosts]}


def main(argv):
    if argv and argv[0] == "img-units":
        for p, (full, ref) in IMG_MINUTES.items():
            print(f"{p}\t{full:g}\t{ref:g}")
        return 0
    if argv and argv[0] == "img-deal":
        import json
        print(json.dumps(img_deal_json(json.load(sys.stdin)), indent=1, sort_keys=True))
        return 0
    if not argv or argv[0] not in ("deal", "table", "units"):
        raise DealError("usage: deal-units.py deal|table|units [--arch LIST] [--only LIST] [--legacy] "
                        "HOST=CLASS=CAPS...")
    what, argv = argv[0], argv[1:]
    arch, only, legacy, args = ("amd64",), None, False, []
    while argv:
        a = argv.pop(0)
        if a == "--arch":
            want_a = (argv.pop(0) if argv else "").split(",")
            arch = ARCHES if "all" in want_a else tuple(x for x in ARCHES if x in want_a)
            if not arch:
                raise DealError("--arch: amd64, arm64, amd64,arm64 or all")
        elif a == "--only":
            only = [x for x in (argv.pop(0) if argv else "").split(",") if x]
            bad = [x for x in only if x not in COMPONENTS]
            if bad or not only:
                raise DealError(f"--only: of {' '.join(COMPONENTS)}, not '{','.join(bad) or ''}'")
        elif a == "--legacy":
            legacy = True
        elif a.startswith("-"):
            raise DealError(f"unknown option {a}")
        else:
            args.append(a)
    if what == "units":
        for u in units_for(arch):
            print("\t".join([u.name, u.component, str(u.minutes), u.kind, " ".join(u.needs), u.checks]))
        return 0
    hosts, skipped = deal([parse_host(a) for a in args], arch, only, legacy)
    if what == "table":
        print("\n".join(table(hosts, skipped)))
        return 0
    for h in hosts:
        print(f"{h.name}\t{','.join(h.names())}")
    for name, why in skipped:
        print(f"skipped\t{name}\t{why}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except DealError as e:
        sys.stderr.write(f"deal-units: {e}\n")
        sys.exit(2)
