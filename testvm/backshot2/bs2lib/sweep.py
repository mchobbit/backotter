"""The option sweep (backshot2.md, "Option sweep"): load's flags, drawn per case.

- --new-uuids, when the bko-holt under test has it (caps["new_uuids"]: tier 0
  finds out once per build; load says "isn't built yet" until it is);
- --no-nvram and --removable on a UEFI source.  Tier 1 boots each VM with a
  fresh NVRAM, so it adds --no-nvram itself there, as HoltTest1 does; tier 2
  keeps the NVRAM and runs exactly the case's flags;
- --fit-largest PCT whenever the target is smaller than the layout's disk
  (load refuses it without), and sometimes when it's bigger;
- --snapshot of an older backup: the case's "snapshots" ({"count": N,
  "restore": I}, I counted from the oldest, 0) has the source backed up N
  times with content changed between, and load's option is "--snapshot @I",
  which the tier 1 runner turns into that snapshot's ID;
- --reformat waits for claude/holt-reformat to merge into main (decision 4).

What each flag changes on purpose, for the judges (B9): expected(case) below.
"""

from . import dumpx, fitcalc


def draw(rng, source, drifted, targets, caps):
    """(load_options, snapshots or None) for a case."""
    opts = []
    uefi = dumpx.firmware(source) == "uefi"
    if caps.get("new_uuids") and rng.random() < 0.15:
        opts.append("--new-uuids")
    if uefi and rng.random() < 0.2:
        opts.append("--removable")
    if uefi and rng.random() < 0.25:
        opts.append("--no-nvram")
    smaller = any(targets[dk["name"]] < dk["size_bytes"] for dk in dumpx.disks(drifted))
    bigger = any(targets[dk["name"]] > dk["size_bytes"] for dk in dumpx.disks(drifted))
    if smaller or (bigger and rng.random() < 0.5):
        need = fitcalc.pct_needed(drifted, targets)
        if need is None:
            return None, None
        pct = min(50, need + rng.choice([0, 0, 1, 2, 5, 10]))
        if pct < need:
            return None, None
        opts += ["--fit-largest", str(max(1, pct))]
    snaps = None
    if rng.random() < 0.15:
        n = rng.choice([2, 3])
        snaps = {"count": n, "restore": rng.randrange(0, n - 1)}
        opts += ["--snapshot", f"@{snaps['restore']}"]
    return opts, snaps


def expected(case):
    """What the judges must allow, from the case's flags: a dict of
    {"new_uuids": bool, "no_nvram": bool, "removable": bool,
     "fit": PCT or None, "snapshot": index or None}."""
    o = case.get("load_options") or []
    fit = None
    if "--fit-largest" in o:
        fit = int(str(o[o.index("--fit-largest") + 1]).rstrip("%"))
    snap = None
    if "--snapshot" in o:
        v = o[o.index("--snapshot") + 1]
        snap = int(v[1:]) if str(v).startswith("@") else v
    return {"new_uuids": "--new-uuids" in o, "no_nvram": "--no-nvram" in o, "removable": "--removable" in o,
            "fit": fit, "snapshot": snap}
