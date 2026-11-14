"""Cases drawn from the corpus dumps, shared by the golden, smoke and
family tests (unittest runs them in one process: each dump's cases are
drawn once).

cases(guest) -> 300 cases, seeds 0-299, caps {}, source GUEST, no generation.
"""

import functools
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BS2 = os.path.dirname(HERE)
if BS2 not in sys.path:
    sys.path.insert(0, BS2)
CORPUS = os.path.normpath(os.path.join(BS2, "..", "..", "holt", "tests", "corpus"))
GOLDEN = os.path.join(HERE, "golden")
SEEDS = 300

from bs2lib import dumpx, families as F, generate as G  # noqa: E402


@functools.lru_cache(maxsize=None)
def dump(guest):
    return dumpx.load(os.path.join(CORPUS, guest, "src.json"))


def guests():
    """Every corpus guest with a dump, by name."""
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(CORPUS, "*", "src.json")))


def linux_guests():
    return [g for g in guests() if F.family_of(g, dump(g)) is not None and F.os_key(dump(g)) == "linux"]


def of_family(name):
    return [g for g in guests() if (F.family_of(g, dump(g)) or F.Family("")).name == name]


@functools.lru_cache(maxsize=None)
def cases(guest):
    d = dump(guest)
    return tuple(G.generate(guest, None, d, seed, caps={}) for seed in range(SEEDS))


def case_digest(case):
    """The golden's hash of a case: its canonical JSON, whole."""
    return hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()[:16]


def golden_of(family, seeds=200):
    """{guest: [hash of seed 0, 1, ...]} for FAMILY's corpus dumps."""
    return {g: [case_digest(c) for c in cases(g)[:seeds]] for g in of_family(family)}


def dump_sum(guest):
    """The sha256 of GUEST's corpus dump: a golden holds only for the dump it was drawn from."""
    import hashlib
    with open(os.path.join(CORPUS, guest, "src.json"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_golden(family):
    from bs2lib import GENERATOR
    g = golden_of(family)
    body = {"family": family, "generator": GENERATOR, "seeds": [0, 200], "caps": {},
            "hash": "sha256(json.dumps(case, sort_keys=True))[:16] of generate(GUEST, None, dump, SEED, caps={})",
            "guests": g, "dumps": {x: dump_sum(x) for x in sorted(g)}}
    with open(os.path.join(GOLDEN, f"{family}.json"), "w") as f:
        json.dump(body, f, indent=0, sort_keys=True)
        f.write("\n")


def edited(d, op, **params):
    """(edit, drifted) for one edit of OP on D."""
    from bs2lib import edits as E
    e = E.build(d, op, params)
    return e, E.apply(d, [e])


def with_type(d, name, fs_type):
    """D with device NAME's content type changed: a drift no phase 1 edit makes (for the layer quirks)."""
    x = dumpx.clone(d)
    dumpx.devices(x)[name]["content"]["type"] = fs_type
    return x


def reasons(found, rule=None):
    return [w for r, w in found if rule is None or r == rule]
