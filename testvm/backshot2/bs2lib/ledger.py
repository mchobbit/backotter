"""BackShot2's rows in Test2's ledger (backshot2.md, "Words"; test2.md, L1).

A row per case and tier, family "bs2", cell "bs2:SOURCE/CASEHASH@tTIER", on
this host's ledger (HOST_ROOT/ledger/HOST.jsonl).  Its key is Test2's,
from the hard inputs: the bko-holt and rustic-bko under test (bin), the
source's generation (guest), and BackShot2's own scripts (harness), so an
unchanged case at an unchanged key needn't run again (done() says).
Verdicts map onto the ledger's: PASS, FAIL and HARNESS as they are; INVALID
(the generator's fault) and UNSUPPORTED (a flag the build hasn't got) are
HARNESS rows with extra.bs2 saying which, never a FAIL.

A row's word is the bs2 word whose run made it: the writer's WORD, else
BS2_WORD in the environment (bs2 sets it to the word it runs and passes it on
to the hosts with the rest of PASS_VARS: BackShot2Alpine, BackShot2 or
BackShot2All), else BackShot2Alpine (DEFAULT_WORD).

Rows are written only for a word that runs (2026-11-25: a fixer's cases by
hand once left 400 HARNESS rows under a word that wasn't running, and the
ledger is append-only).  write_rows() writes when (recording()):
  - BS2_NO_LEDGER is set: never (it always wins; leftovers and shrink set it);
  - BS2_WORD (or WORD) is one of WORDS: yes;
  - BS2_WORD is anything else: no (a row would name a word that doesn't exist);
  - BS2_WORD unset and BS2_LEDGER=1: yes, as DEFAULT_WORD (a case by hand that
    is meant to count);
  - else (a case by hand: replay, soak, shrink, a fixer's tier1): no.
Every no but BS2_NO_LEDGER's says so on stderr, once a process, and why.
done() (the PASS skip) reads the ledger whatever these say.
"""

import glob
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTVM = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(TESTVM, "test2"))

HARNESS_FILES = ("backshot2/bs2lib/*.py", "backshot2/bs2-host", "holttest1d/ht1.sh", "holttest1d/ht1-lib.sh",
                 "holttest1d/judge.py", "compare/fsmeta.py")


def harness():
    out = {}
    for pat in HARNESS_FILES:
        for p in sorted(glob.glob(os.path.join(TESTVM, pat))):
            with open(p, "rb") as f:
                out["testvm/" + os.path.relpath(p, TESTVM)] = "sha256:" + hashlib.sha256(f.read()).hexdigest()
    return out


def cell(source, case_hash, tier):
    return f"bs2:{source.replace('run:', 'run-')}/{case_hash}@t{tier}"


WORD_ENV = "BS2_WORD"
DEFAULT_WORD = "BackShot2Alpine"
# The bs2 words whose runs write rows (bs2's main(): BackShot2Alpine and BackShot2; BackShot2All
# is bs2lib/allrun.py's WORD; t2lib/batchkinds.py's BS2_WORDS repeats them for a batch's resume).
WORDS = (DEFAULT_WORD, "BackShot2", "BackShot2All")
LEDGER_ENV = "BS2_LEDGER"        # =1: write a case by hand's rows too (as DEFAULT_WORD without BS2_WORD)
NO_LEDGER_ENV = "BS2_NO_LEDGER"  # set: never write (wins over both)
_noted = []                      # (the note said already, in this process)


def word_of(word=None):
    """The word a row names: WORD, else $BS2_WORD, else DEFAULT_WORD."""
    return word or os.environ.get(WORD_ENV) or DEFAULT_WORD


def inputs(source, gen, bins):
    """The row's inputs.  The guest group: SOURCE's generation GEN (gen:HEX or img:HEX, else
    ver: of it); a slimmed source's (bs2lib/slim.py: GEN's "+slimN" after the guest's own) as
    the guest's generation and, beside it, SOURCE#recipe: ver:slimN, so its rows name the
    slimmed source (another key than the full guest's) in values Test2's ledger takes."""
    base, plus, recipe = (gen or "").partition("+")
    guest = {}
    if base.startswith(("gen:", "img:")):
        guest[source] = base
        if plus:
            guest[f"{source}#recipe"] = f"ver:{recipe}"
    else:
        guest[source] = f"ver:{gen or source}"
    return {"bin": dict(bins), "guest": guest, "harness": harness()}


def rows(run, source, gen, results, tier, wall, bins, word=None):
    from t2lib import ledger, utc_now
    from bs2lib import case as C
    word = word_of(word)
    inp = inputs(source, gen, bins)
    out = []
    per = int(wall / max(1, len(results)))
    for name, case, verdict, why in results:
        v, extra = verdict, {}
        if verdict in ("INVALID", "UNSUPPORTED"):
            v, extra = "HARNESS", {"bs2": verdict}
        cls = {"PASS": "pass", "FAIL": "fail", "HARNESS": "harness"}[v]
        h = C.case_hash(case)
        out.append(ledger.complete({
            "origin": f"bs2:{run}:{name}:t{tier}", "family": "bs2", "cell": cell(source, h, tier), "kind": "run",
            "run": run, "word": word, "rule": "bs2", "attempt": 1, "inputs": inp, "valid": True, "verdict": v,
            "class": cls, "commit": None, "committed": False, "copy": "bs2-wt", "start": utc_now(),
            "fails": [str(x)[:200] for x in why[:5]] if v != "PASS" else [], "wall_s": per,
            "extra": dict(extra, case=name, tier=tier)}))
    return out


def recording(word=None):
    """(whether write_rows() writes, why not: None when it does or BS2_NO_LEDGER says no);
    the rule is the module's docstring's."""
    if os.environ.get(NO_LEDGER_ENV):
        return False, None
    w = word or os.environ.get(WORD_ENV)
    if w:
        if w in WORDS:
            return True, None
        return False, (f"{WORD_ENV}={w!r} isn't a BackShot2 word ({', '.join(WORDS)}): set it to the word that runs, "
                       f"or unset it and set {LEDGER_ENV}=1 to record a case by hand as {DEFAULT_WORD}")
    if os.environ.get(LEDGER_ENV) == "1":
        return True, None
    return False, (f"{WORD_ENV} isn't set, so no BackShot2 word is running (a case by hand); "
                   f"set {LEDGER_ENV}=1 to record them (word {DEFAULT_WORD})")


def write_rows(run, source, gen, results, tier, wall, bins, word=None):
    """Appends the rows when recording() says so (else a note on stderr, once a process);
    best effort (a ledger problem is noted, never fatal)."""
    ok, why = recording(word)
    if not ok:
        if why and not _noted:
            _noted.append(why)
            print(f"bs2: no ledger rows written (run {run}, {source}, tier {tier}; none in this process): {why}",
                  file=sys.stderr)
        return
    try:
        from t2lib import ledger, host_ledger_file as ledger_path
        rs = rows(run, source, gen, results, tier, wall, bins, word)
        bad = [e for r in rs for e in ledger.validate(r)]
        if bad:
            print(f"bs2: ledger rows not written: {bad[:3]}", file=sys.stderr)
            return
        ledger.Ledger(ledger_path()).append_rows(rs)
    except Exception as e:  # noqa: BLE001 - the ledger is a record, not the test
        print(f"bs2: ledger: {e}", file=sys.stderr)


def done(source, case_hash, tier, gen, bins, path=None):
    """Whether this host's ledger has a PASS at this case's key."""
    try:
        from t2lib import keys, ledger, host_ledger_file as ledger_path
        k = keys.key(cell(source, case_hash, tier), inputs(source, gen, bins))
        for r in ledger.read(path or ledger_path()).rows:
            if r.get("key") == k and r.get("verdict") == "PASS":
                return True
    except Exception:  # noqa: BLE001
        return False
    return False
