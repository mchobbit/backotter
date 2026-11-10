"""The shrinker (backshot2.md, "Replay, shrinking and the corpus"): a failing
case down to the smallest one that still fails at the same tier with the
same failing check.

  1. delta-debug the case's parts (each edit, each load option group, the
     content drift): drop halves, then single parts, while it still fails;
  2. move each remaining numeric value halfway back towards the source's
     (edits.Op.numeric, Op.source), while it still fails;
  3. with content drift left, delta-debug its entries ("only") the same way.

Every candidate is rebuilt from its edits' own values (rebuild: each edit's
sets worked out again on the layout before it), checked against the three
rules, and tried only if valid: a shrink never makes an INVALID case.
STILL_FAILS(case) -> bool runs it (tier 0 in-process; tier 1 or 2 in a VM).
"""

from . import content as CT, dumpx, edits as E, fitcalc, rules
from .geometry import Invalid

PARAMS_SKIP = ("op", "sets", "by_extents")


def params_of(e):
    return {k: v for k, v in e.items() if k not in PARAMS_SKIP}


def rebuild(source, edits):
    """EDITS rebuilt in order on SOURCE, or None when one can't be."""
    cur, out = source, []
    for e in edits:
        try:
            n = E.build(cur, e["op"], params_of(e))
        except (Invalid, KeyError, TypeError, ValueError, StopIteration):
            return None
        cur = E.apply(cur, [n])
        out.append(n)
    return out


def _opt_groups(opts):
    """load_options as droppable groups: a flag with its value."""
    out, i = [], 0
    while i < len(opts):
        if opts[i] in ("--fit-largest", "--snapshot") and i + 1 < len(opts):
            out.append(opts[i:i + 2])
            i += 2
        else:
            out.append([opts[i]])
            i += 1
    return out


def assemble(base, source, parts):
    """A case from BASE with only PARTS ([("edit", e) | ("opt", [..]) | ("content", c)]), or None if invalid."""
    edits = rebuild(source, [p[1] for p in parts if p[0] == "edit"])
    if edits is None:
        return None
    opts = [x for p in parts if p[0] == "opt" for x in p[1]]
    case = {k: v for k, v in base.items() if k not in ("edits", "load_options", "content", "target", "snapshots", "failed", "name")}
    case["edits"] = edits
    content = next((p[1] for p in parts if p[0] == "content"), None)
    if content:
        case["content"] = content
    if "--snapshot" in opts and base.get("snapshots"):
        case["snapshots"] = base["snapshots"]
    drifted = E.apply(source, edits)
    targets = {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(drifted)}
    for e in edits:
        if e["op"] == "target_size":
            targets[e["disk"]] += e["by_bytes"]
    smaller = any(targets[dk["name"]] < dk["size_bytes"] for dk in dumpx.disks(drifted))
    if smaller and "--fit-largest" not in opts:
        return None
    if "--fit-largest" in opts:
        i = opts.index("--fit-largest")
        need = fitcalc.pct_needed(drifted, targets) if smaller else 1
        if need is None:
            return None
        if int(opts[i + 1]) < need:
            opts[i + 1] = str(need)
    case["load_options"] = opts
    if targets != {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(drifted)}:
        case["target"] = {k: {"size_bytes": v} for k, v in sorted(targets.items())}
    from .generate import planted_of
    if rules.check(source, drifted, edits, planted_of(source, content)):
        return None
    return case


def ddmin(items, test):
    """The smallest sublist of ITEMS for which TEST(sublist) holds (TEST(items) does)."""
    n = 2
    while len(items) >= 2:
        chunk = max(1, len(items) // n)
        subsets = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        progress = False
        for i in range(len(subsets)):
            rest = [x for j, s in enumerate(subsets) if j != i for x in s]
            if rest and test(rest):
                items, n, progress = rest, max(2, n - 1), True
                break
        if not progress:
            if n >= len(items):
                break
            n = min(len(items), n * 2)
    if len(items) == 1 and test([]):
        return []
    return items


class Shrinker:
    def __init__(self, source, still_fails, budget=40, log=print):
        self.source, self.still_fails, self.budget, self.log = source, still_fails, budget, log
        self.runs = 0
        self.seen = {}

    def test_case(self, case):
        if case is None:
            return False
        from .case import case_hash
        h = case_hash(case)
        if h in self.seen:
            return self.seen[h]
        if self.runs >= self.budget:
            return False
        self.runs += 1
        r = bool(self.still_fails(case))
        self.seen[h] = r
        self.log(f"  try {self.runs}: {len(case['edits'])} edits, options {case.get('load_options')}, content {bool(case.get('content'))}: {'still fails' if r else 'passes'}")
        return r

    def shrink(self, case):
        parts = [("edit", e) for e in case.get("edits") or []] + [("opt", g) for g in _opt_groups(case.get("load_options") or [])]
        if case.get("content"):
            parts.append(("content", case["content"]))
        best = case
        # 1. the parts
        keep = ddmin(parts, lambda ps: self.test_case(assemble(case, self.source, ps)))
        cand = assemble(case, self.source, keep)
        if cand is not None and self.test_case(cand):
            best = cand
        # 2. numbers halfway back towards the source's
        changed = True
        while changed and self.runs < self.budget:
            changed = False
            for i, e in enumerate(best["edits"]):
                op = E.OPS[e["op"]]
                for f in op.numeric:
                    if f not in e:
                        continue
                    before = E.apply(self.source, best["edits"][:i])
                    target = op.source(before, e, f)
                    v = e[f]
                    if v == target:
                        continue
                    half = target + (v - target) // 2
                    if e["op"] == "target_size":
                        ss = dumpx.sector(dumpx.disk(before, e["disk"]))
                        half = half // ss * ss
                    if half == v or (e["op"] in ("gap_before", "align") and half < 1):
                        continue
                    ne = dict(e, **{f: half})
                    if e["op"] == "lv_resize":
                        v_ = dumpx.vg(before, e["vg"])
                        try:
                            ne["segments"] = E.resize_lv_pieces(v_, dumpx.lv(v_, e["lv"]), half)
                        except Invalid:
                            continue
                    edits = best["edits"][:i] + [ne] + best["edits"][i + 1:]
                    parts = [("edit", x) for x in edits] + [("opt", g) for g in _opt_groups(best.get("load_options") or [])]
                    if best.get("content"):
                        parts.append(("content", best["content"]))
                    c2 = assemble(best, self.source, parts)
                    if c2 is not None and self.test_case(c2):
                        best, changed = c2, True
                        break
                if changed:
                    break
        # 3. the content drift's entries
        if best.get("content") and self.runs < self.budget:
            fs = "ext4"
            entries = CT.plan(best["content"], fs)
            idx = [x["i"] for x in entries]

            def with_only(ix):
                c = dict(best, content=dict(best["content"], only=sorted(ix)))
                return self.test_case(c)
            keep = ddmin(idx, with_only)
            if keep != idx:
                best = dict(best, content=dict(best["content"], only=sorted(keep)))
        return best
