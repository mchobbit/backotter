"""The case file (backshot2.md, "A case"): testvm/backshot2/cases/NAME.case.

    {"source": "alpine324-uefi@gen:1a2b...", "seed": 4217, "generator": "1",
     "edits": [...], "target": {"vda": {"size_bytes": 17179869184}},
     "load_options": ["--fit-largest", "10"],
     "content": {"seed": 4217, "recipe": "alpine-small-1"}}

- source names the dump and the backup that go with it: SOURCE@GEN (a
  guest's generation: gen:HEX from image-state, img:HEX its image's sum)
  or run:NAME (a BackShot1 run in the Thinkpad's library).
- edits is the whole change; replay() applies them to the source's dump
  (edits.apply, a pure function), so the drifted layout is never stored.
- seed and generator say how it was made; replay uses neither.
- target and load_options: what to restore onto, and how.
- content: the content drift (content.py), or absent.
Other keys a case may carry, never part of its hash: "name", "tier" (the
highest tier it needs), "note", and for a shrunk failure "failed"
({tier, check, excerpt}) and "from" (the case it was shrunk from).
"""

import hashlib
import json
import os

from . import dumpx, edits as E

HASHED = ("source_name", "edits", "target", "load_options", "content", "snapshots")


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def source_name(case):
    s = case["source"]
    if s.startswith("run:"):
        return s
    return s.split("@", 1)[0]


def source_gen(case):
    s = case["source"]
    return s.split("@", 1)[1] if "@" in s and not s.startswith("run:") else None


def case_hash(case):
    """What a case does, hashed: its source (by name: the generation is the
    ledger's own key), edits, target, options and content.  16 hex."""
    body = {"source_name": source_name(case), "edits": case.get("edits") or [], "target": case.get("target") or {},
            "load_options": case.get("load_options") or [], "content": case.get("content"), "snapshots": case.get("snapshots")}
    return hashlib.sha256(canonical(body).encode()).hexdigest()[:16]


def read(path):
    with open(path) as f:
        c = json.load(f)
    validate(c, path)
    return c


def text(case):
    """A case file's text: the keys in a fixed order, one edit per line."""
    order = ["name", "source", "seed", "generator", "tier", "edits", "target", "load_options", "content", "failed", "from", "note"]
    keys = [k for k in order if k in case] + sorted(k for k in case if k not in order)
    lines = []
    for k in keys:
        if k == "edits":
            es = case[k]
            if not es:
                lines.append(' "edits": []')
            else:
                lines.append(' "edits": [\n' + ",\n".join("   " + json.dumps(e, sort_keys=True) for e in es) + "]")
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(case[k], sort_keys=True)}")
    return "{\n" + ",\n".join(lines) + "}\n"


def write(path, case):
    validate(case, path)
    tmp = f"{path}.part"
    with open(tmp, "w") as f:
        f.write(text(case))
    os.replace(tmp, path)


def validate(c, where="case"):
    for k in ("source", "edits"):
        if k not in c:
            raise ValueError(f"{where}: no {k}")
    if not isinstance(c["edits"], list) or not all(isinstance(e, dict) and e.get("op") in E.OPS for e in c["edits"]):
        raise ValueError(f"{where}: edits must be a list of known ops ({', '.join(E.OPS)})")
    if not isinstance(c.get("load_options", []), list):
        raise ValueError(f"{where}: load_options is a list")


def targets(case, drifted):
    """{disk: target bytes}: the case's target where it says, else the drifted disk's size."""
    out = {dk["name"]: dk["size_bytes"] for dk in dumpx.disks(drifted)}
    for e in case.get("edits") or []:
        if e.get("op") == "target_size":
            out[e["disk"]] = out[e["disk"]] + e["by_bytes"]
    for k, v in (case.get("target") or {}).items():
        out[k] = v["size_bytes"]
    return out


def replay(case, source_dump):
    """The drifted layout: the source's dump with the case's edits applied."""
    return E.apply(source_dump, case.get("edits") or [])


def name_of(case):
    return case.get("name") or f"{source_name(case).replace('run:', 'run-')}-{case_hash(case)}"


def touches_esp(case, source_dump):
    """Whether a case moves or renumbers an ESP, or asks for what only
    tier 2 sees (NVRAM kept): --no-nvram or --removable on a UEFI source."""
    esps = dumpx.esp_devices(source_dump)
    if dumpx.firmware(source_dump) == "uefi" and {"--no-nvram", "--removable"} & set(case.get("load_options") or []):
        return True
    cur = source_dump
    for e in case.get("edits") or []:
        if e.get("op") in ("gap_before", "align", "renumber", "resize", "swap_order"):
            dk = dumpx.disk(cur, e.get("disk"))
            nums = {"renumber": [e.get("from")], "swap_order": [e.get("a"), e.get("b")]}.get(e["op"], [e.get("partition")])
            moved = set()
            for n in nums:
                p = dumpx.part(dk, n)
                if p:
                    moved.add(p["name"])
            if e["op"] == "gap_before":
                p0 = dumpx.part(dk, e["partition"])
                moved |= {q["name"] for q in dk["partitions"] if p0 and q["start_lba"] >= p0["start_lba"]}
            if moved & esps:
                return True
        cur = E.apply(cur, [e])
        esps = dumpx.esp_devices(cur)
    return False
