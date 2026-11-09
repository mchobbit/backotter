"""The Test2 words: what the user typed, parsed (contract section 2, rulings R27).

    GateTest2 [ROUND] [LAYER...] [stale] [plan]      GateTest2All [ROUND] [plan]
    SticTest2 [ROUND] [GUEST...] [stale] [plan]      SticTest2All [ROUND] [plan]
    (WORD All is the same as WORDAll)
    HoltTest2 [GUEST[+VARIANT]...] [stale] [plan] [+bigger] [+oneshot] [+degraded]
    HoltTest2X [X...] [...]  for X in D A M U Z F C V BSD H I Obscure      HoltTest2All [plan]

parse() only reads the tokens and checks the names against `known`; which cells
a word selects is plan.py's business (the contract keeps family scope there).
A refusal raises Refused, which the CLI turns into exit 2.
"""

import re

FAMILY_WORD = {"gate": "GateTest2", "stic": "SticTest2", "holt": "HoltTest2"}
HOLT_LETTERS = ("D", "A", "M", "U", "Z", "F", "C", "V", "BSD", "H", "I", "Obscure")
VARIANTS = ("bigger", "oneshot", "degraded")
REFUSED_VARIANTS = {"close": "+close waits for H3 (throttled closes under load)",
                    "img": "+img waits for I1 (a real rescue image as rescuer)"}
OUT_OF_SCOPE = {
    "guesttest2": "GuestTest2 is not built yet (out of this build's scope: it waits for V1 and P1)",
    "imgtest2": "ImgTest2 is not built yet (out of this build's scope: it waits for I1a)",
    "bkotest2": "BkoTest2 is not built yet (out of this build's scope)",
    "sweeptest2": "SweepTest2 is not built yet (out of this build's scope; its cadence is policy.sweep in classes.json)",
}
KEYWORDS = ("plan", "stale")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_WORD = re.compile(r"(gatetest2|stictest2|holttest2)(all)?(.*)\Z", re.I)
_LETTER = {x.lower(): x for x in HOLT_LETTERS}


class Refused(Exception):
    """A word that can't run: the CLI prints it and exits 2."""


class Word:
    """A parsed word.  family: gate, stic or holt.  form: bare, names or All.

    names     explicit cells (stic:G, holt:G[+V], gate:L[@H]), in the order typed
    letters   holt family letters (HoltTest2D 2M -> D, M)
    prefixes  gate layer prefixes as typed (fork, bin, loop:linux, ...)
    variants  holt variant words (+bigger ...)
    """

    def __init__(self, family, text):
        self.family, self.text = family, text
        self.all = False
        self.round = None
        self.names, self.letters, self.prefixes, self.variants = [], [], [], []
        self.stale = self.plan = False
        self.notes = []

    @property
    def form(self):
        if self.all:
            return "All"
        if self.names or self.letters or self.prefixes:
            return "names"
        return "bare"

    @property
    def word(self):
        """The canonical word: HoltTest2D, SticTest2All, GateTest2 ..."""
        w = FAMILY_WORD[self.family]
        if self.all:
            return w + "All"
        if self.family == "holt" and self.letters:
            return w + self.letters[0]
        return w

    def to_json(self):
        return dict(family=self.family, word=self.word, text=self.text, form=self.form, all=self.all,
                    round=self.round, names=list(self.names), letters=list(self.letters),
                    prefixes=list(self.prefixes), variants=list(self.variants), stale=self.stale,
                    plan=self.plan, notes=list(self.notes))

    def __repr__(self):
        return f"Word({self.text!r})"


def is_word(tok):
    """True for a Test2 word (any family, in or out of scope)."""
    t = tok.lower()
    return bool(_WORD.match(t)) or t in OUT_OF_SCOPE or (t.endswith("all") and t[:-3] in OUT_OF_SCOPE)


def parse(argv, known):
    """argv: the word and its arguments (the CLI's options already taken out).

    known: {"stic": [guest...],
            "holt": {"sources": [...], "variants": {V: [guest...]}, "families": {X: [...]},
                     "refused": {guest: why}},
            "gate": [layer cell names without "gate:", e.g. holt, fork:restic, loop:linux@chopin]}
    """
    if not argv:
        raise Refused("no word: GateTest2, SticTest2 or HoltTest2 (with All, a family letter, names, stale or plan)")
    head, args = argv[0], list(argv[1:])
    low = head.lower()
    for oos, why in OUT_OF_SCOPE.items():
        if low == oos or low == oos + "all":
            raise Refused(why)
    m = _WORD.match(head)
    if not m:
        raise Refused(f"unknown word {head!r}: GateTest2, SticTest2 or HoltTest2 (and their All forms)")
    fam = {"gatetest2": "gate", "stictest2": "stic", "holttest2": "holt"}[m.group(1).lower()]
    w = Word(fam, " ".join(argv))
    w.all = bool(m.group(2))
    suffix = m.group(3)
    if args and args[0].lower() == "all" and not suffix:   # "SticTest2 All" is SticTest2All
        w.all = True
        args.pop(0)
    if suffix:
        if fam != "holt" or w.all:
            raise Refused(f"unknown word {head!r}")
        x = _LETTER.get(suffix.lower())
        if not x:
            raise Refused(f"no HoltTest2 family {suffix!r}: the letters are {' '.join(HOLT_LETTERS)}")
        w.letters.append(x)
    if fam == "stic":
        _stic(w, args, known)
    elif fam == "holt":
        _holt(w, args, known)
    else:
        _gate(w, args, known)
    return w


def _keyword(w, tok):
    t = tok.lower()
    if t == "plan":
        w.plan = True
        return True
    if t == "stale":
        if w.all:
            raise Refused(f"{w.word} runs everything fresh: 'stale' adds nothing to it")
        w.stale = True
        return True
    return False


def _round(w, tok, what):
    if w.round is not None:
        raise Refused(f"{tok!r} is no {what} (and the round is already {w.round!r})")
    if not NAME.match(tok):
        raise Refused(f"bad ROUND {tok!r}: letters, digits, '.', '_' and '-', starting with a letter or digit")
    w.round = tok


def _stic(w, args, known):
    guests = set(known.get("stic") or ())
    for tok in args:
        if _keyword(w, tok):
            continue
        g = tok[5:] if tok.startswith("stic:") else tok
        if g in guests:
            if w.all:
                raise Refused(f"SticTest2All runs all {len(guests)} checks: no guest names ({g})")
            if f"stic:{g}" not in w.names:
                w.names.append(f"stic:{g}")
        elif tok.startswith("stic:"):
            raise Refused(f"no SticTest check {g!r} (no testvm/stictest1/{g}/check.sh)")
        else:
            _round(w, tok, "SticTest check")


def _holt(w, args, known):
    k = known.get("holt") or {}
    sources = set(k.get("sources") or ())
    variants = k.get("variants") or {}
    refused = k.get("refused") or {}
    for tok in args:
        if _keyword(w, tok):
            continue
        t = tok[5:] if tok.startswith("holt:") else tok
        if t.startswith("+"):
            v = t[1:].lower()
            if v in REFUSED_VARIANTS:
                raise Refused(REFUSED_VARIANTS[v])
            if v not in VARIANTS:
                raise Refused(f"no variant {t!r}: +bigger, +oneshot or +degraded")
            if w.all:
                raise Refused("HoltTest2All already runs every variant cell")
            if v not in w.variants:
                w.variants.append(v)
            continue
        lt = t[1:] if t[:1] == "2" else t
        if lt.lower() in _LETTER and t not in sources:
            if w.all:
                raise Refused("HoltTest2All already runs every family")
            x = _LETTER[lt.lower()]
            if x not in w.letters:
                w.letters.append(x)
            continue
        g, _, v = t.partition("+")
        if v:
            vl = v.lower()
            if vl in REFUSED_VARIANTS:
                raise Refused(REFUSED_VARIANTS[vl])
            if vl not in VARIANTS:
                raise Refused(f"no variant +{v} (in {tok!r}): +bigger, +oneshot or +degraded")
        if g in refused:
            raise Refused(f"{g} is no HoltTest2 source: {refused[g]}")
        if g not in sources:
            if NAME.match(g) and not v:
                raise Refused(f"no HoltTest2 source {g!r} (classes.json holt.sources)")
            raise Refused(f"no HoltTest2 cell {tok!r}")
        if w.all:
            raise Refused(f"HoltTest2All runs all of them: no guest names ({tok})")
        if v:
            if g not in (variants.get(v.lower()) or ()):
                raise Refused(f"+{v.lower()} doesn't apply to {g} (classes.json holt.variants: "
                              f"{', '.join(variants.get(v.lower()) or ()) or 'none'})")
            cell = f"holt:{g}+{v.lower()}"
        else:
            cell = f"holt:{g}"
        if cell not in w.names:
            w.names.append(cell)


def _gate(w, args, known):
    layers = list(known.get("gate") or ())
    for tok in args:
        if _keyword(w, tok):
            continue
        t = tok[5:] if tok.startswith("gate:") else tok
        hit = [l for l in layers if l == t or l.startswith(t + ":") or l.startswith(t + "@")]
        if hit:
            if w.all:
                raise Refused(f"GateTest2All runs every layer: no layer names ({tok})")
            if t not in w.prefixes:
                w.prefixes.append(t)
            for l in hit:
                if f"gate:{l}" not in w.names:
                    w.names.append(f"gate:{l}")
        elif tok.startswith("gate:"):
            raise Refused(f"no gate layer {t!r} (testvm/test2/gate/layers.json)")
        else:
            _round(w, tok, "gate layer")


def split_plan_form(argv):
    """`t2 plan WORD ARGS` is `t2 WORD ARGS plan`."""
    if argv and argv[0] == "plan" and len(argv) > 1 and is_word(argv[1]):
        return argv[1:] + ["plan"]
    return argv
