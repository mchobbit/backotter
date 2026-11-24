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


# The shape a guest has on the farm where its corpus dump differs in what the draws read:
# the guest was updated after its dump (images take OS updates; guix15 and guix15-bios
# 2026-11-24, docs/design/guix.md).  Each is made from the corpus dump, so it carries the
# corpus's pseudonyms and no identifier of the live guest.  The goldens hold their draws
# too (as_run), and the family tests draw from them: the farm's runs draw from the live
# dumps (/bkoscratch/backshot2/sources/SOURCE/src.json), not the corpus's.
#
# Guix, as the live sources were on chopin and cinqcent on 2026-11-24 (bs2a-20261124-1111):
# a second generation (a guix pull and reconfigure: the same kernel, initrd and devices, a
# new system), the current one; grub.cfg's "old configurations" entry for the first; and on
# guix15 no "Guix" NVRAM entry (the updated guest boots its fallback path on fresh firmware
# variables, through "UEFI Misc Device"), so nothing names the ESP by start and number.
GUIX_SYSTEM_2 = {"guix15": "/gnu/store/8f9mxryhr5syazmgryfcvfhz5hmmamzs-system",
                 "guix15-bios": "/gnu/store/zmh7bikrwhbb7a42vxzf3919vb0r117q-system"}


def guix_as_run(guest, d):
    x = dumpx.clone(d)
    b = x["boot"]
    st = b["store"]
    one = st["generations"][0]
    new = GUIX_SYSTEM_2[guest]
    st["generations"] = [one, dict(one, number=2, system=new)]
    st["current"] = 2
    for k in ("system", "profile"):
        if isinstance(st.get(k), str) and one["system"] in st[k]:
            st[k] = st[k].replace(one["system"], new)
    cl = b.get("cmdline") or {}
    if cl.get("raw"):
        cl["raw"] = cl["raw"].replace(one["system"], new)
    cl["words"] = [w.replace(one["system"], new) for w in cl.get("words") or []]
    cfg = ((b.get("grub") or {}).get("grub_cfg") or {})
    if cfg.get("entries"):
        cfg["entries"] = cfg["entries"][:1] + [f"GNU system, old configurations...>{one['title']} (#1)"] + cfg["entries"][1:]
    efi = b.get("efi")
    if efi:
        gone = {e["number"] for e in efi["entries"] if e.get("file")}
        efi["entries"] = [e for e in efi["entries"] if e["number"] not in gone]
        efi["boot_order"] = [n for n in efi["boot_order"] if n not in gone]
        efi["boot_current"] = next(e["number"] for e in efi["entries"] if e["description"] == "UEFI Misc Device")
        for e in efi["entries"]:
            if e["number"] in efi["boot_order"]:
                e["in_boot_order"] = efi["boot_order"].index(e["number"])
        b["loader"]["evidence"] = ["an ESP holds grubx64.efi"]
    return x


AS_RUN = {"guix15": guix_as_run, "guix15-bios": guix_as_run}


@functools.lru_cache(maxsize=None)
def dump_as_run(guest):
    """GUEST's dump as the farm has it (AS_RUN), or its corpus dump."""
    f = AS_RUN.get(guest)
    return f(guest, dump(guest)) if f else dump(guest)


@functools.lru_cache(maxsize=None)
def cases_as_run(guest):
    d = dump_as_run(guest)
    return tuple(G.generate(guest, None, d, seed, caps={}) for seed in range(SEEDS))


def write_golden(family):
    from bs2lib import GENERATOR
    g = golden_of(family)
    body = {"family": family, "generator": GENERATOR, "seeds": [0, 200], "caps": {},
            "hash": "sha256(json.dumps(case, sort_keys=True))[:16] of generate(GUEST, None, dump, SEED, caps={})",
            "guests": g, "dumps": {x: dump_sum(x) for x in sorted(g)}}
    run = sorted(x for x in g if x in AS_RUN)
    if run:
        # The same seeds over the farm's shape of each (AS_RUN), for the same corpus dumps.
        body["as_run"] = {x: [case_digest(c) for c in cases_as_run(x)[:200]] for x in run}
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
