"""The Test2 words: what the user typed, parsed (contract section 2, rulings R27).

    GateTest2 [ROUND] [LAYER...] [stale] [plan]      GateTest2All [ROUND] [plan]
    SticTest2 [ROUND] [GUEST...] [stale] [plan]      SticTest2All [ROUND] [plan]
    (WORD All is the same as WORDAll)
    HoltTest2 [GUEST[+VARIANT]...] [stale] [plan] [+bigger] [+oneshot] [+degraded] [+img]
    HoltTest2X [X...] [...]  for X in D A M U Z F C V BSD H I Obscure      HoltTest2All [plan]
    GuestTest2 [GUEST...] [stale] [plan]             GuestTest2All [plan]
    GuestTest2 update [GUEST...] [plan]              the real update round
    ImgTest2 [PLAT...] [release] [stale] [plan]      ImgTest2All [release] [plan]
    BkoTest2 [ROUND] [stale] [plan]                  SweepTest2 [ROUND] [plan]

parse() only reads the tokens and checks the names against `known`; which cells
a word selects is plan.py's business (the contract keeps family scope there).
A refusal raises Refused, which the CLI turns into exit 2.
"""

import re

FAMILY_WORD = {"gate": "GateTest2", "stic": "SticTest2", "holt": "HoltTest2", "guest": "GuestTest2", "img": "ImgTest2"}
COMPOSITE_WORD = {"bko": "BkoTest2", "sweep": "SweepTest2"}
HOLT_LETTERS = ("D", "A", "M", "U", "Z", "F", "C", "V", "BSD", "H", "I", "Obscure")
VARIANTS = ("bigger", "oneshot", "degraded", "img")
REFUSED_VARIANTS = {"close": "+close waits for H3 (throttled closes under load)"}
PLATS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
KEYWORDS = ("plan", "stale")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_WORD = re.compile(r"(gatetest2|stictest2|holttest2|guesttest2|imgtest2|bkotest2|sweeptest2)(all)?(.*)\Z", re.I)
_FAMILY = {"gatetest2": "gate", "stictest2": "stic", "holttest2": "holt", "guesttest2": "guest", "imgtest2": "img",
           "bkotest2": "bko", "sweeptest2": "sweep"}
_LETTER = {x.lower(): x for x in HOLT_LETTERS}
_NO_WORD = "GateTest2, SticTest2, HoltTest2, GuestTest2, ImgTest2, BkoTest2 or SweepTest2"


class Refused(Exception):
    """A word that can't run: the CLI prints it and exits 2."""


class Word:
    """A parsed word.  family: gate, stic, holt, guest, img, or the composites bko and sweep.
    form: bare, names or All.

    names     explicit cells (stic:G, holt:G[+V], gate:L[@H], guest:G, img:P), in the order typed
    letters   holt family letters (HoltTest2D 2M -> D, M)
    prefixes  gate layer prefixes as typed (fork, bin, loop:linux, ...)
    variants  holt variant words (+bigger ...)
    update    GuestTest2 update: the real update round
    release   ImgTest2 release: the panel is required
    sweep     SweepTest2, and the children it makes (rule fresh:Sweep, the sweep's budget)
    """

    def __init__(self, family, text):
        self.family, self.text = family, text
        self.all = False
        self.round = None
        self.names, self.letters, self.prefixes, self.variants = [], [], [], []
        self.stale = self.plan = False
        self.update = self.release = self.sweep = False
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
        w = FAMILY_WORD.get(self.family) or COMPOSITE_WORD[self.family]
        if self.all:
            return w + "All"
        if self.family == "holt" and self.letters:
            return w + self.letters[0]
        return w

    def to_json(self):
        return dict(family=self.family, word=self.word, text=self.text, form=self.form, all=self.all,
                    round=self.round, names=list(self.names), letters=list(self.letters),
                    prefixes=list(self.prefixes), variants=list(self.variants), stale=self.stale,
                    plan=self.plan, update=self.update, release=self.release, sweep=self.sweep,
                    notes=list(self.notes))

    def __repr__(self):
        return f"Word({self.text!r})"


def is_word(tok):
    """True for a Test2 word (any family)."""
    return bool(_WORD.match(tok))


def parse(argv, known):
    """argv: the word and its arguments (the CLI's options already taken out).

    known: {"stic": [guest...],
            "holt": {"sources": [...], "variants": {V: [guest...]}, "families": {X: [...]},
                     "refused": {guest: why}},
            "gate": [layer cell names without "gate:", e.g. holt, fork:restic, loop:linux@chopin],
            "guest": [guest...], "guest_refused": {guest: why}, "img": [plat...]}
    """
    if not argv:
        raise Refused(f"no word: {_NO_WORD} (with All, a family letter, names, stale or plan)")
    head, args = argv[0], list(argv[1:])
    m = _WORD.match(head)
    if not m:
        raise Refused(f"unknown word {head!r}: {_NO_WORD} (and their All forms)")
    fam = _FAMILY[m.group(1).lower()]
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
    {"stic": _stic, "holt": _holt, "gate": _gate, "guest": _guest, "img": _img,
     "bko": _composite, "sweep": _composite}[fam](w, args, known)
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
                raise Refused(f"no variant {t!r}: +bigger, +oneshot, +degraded or +img")
            if w.all:
                raise Refused("HoltTest2 +img runs the image cells; HoltTest2All has none" if v == "img"
                              else "HoltTest2All already runs every variant cell")
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
                raise Refused(f"no variant +{v} (in {tok!r}): +bigger, +oneshot, +degraded or +img")
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
    if "img" in w.variants and not w.names and not w.letters:   # +img alone: those cells, all of them, fresh
        w.variants.remove("img")
        w.names += [f"holt:{g}+img" for g in variants.get("img") or ()]


def _guest(w, args, known):
    guests = set(known.get("guest") or ())
    refused = known.get("guest_refused") or {}
    for tok in args:
        if tok.lower() == "update":
            if w.all:
                raise Refused("GuestTest2All is the audit and merges nothing: GuestTest2 update is the update round")
            w.update = True
            continue
        if _keyword(w, tok):
            continue
        g = tok[6:] if tok.startswith("guest:") else tok
        if g in refused:
            raise Refused(f"{g} is no GuestTest2 guest: {refused[g]}")
        if g not in guests:
            raise Refused(f"no GuestTest2 guest {g!r} (testvm/test2/guest/spec.json)")
        if w.all:
            raise Refused(f"GuestTest2All runs all {len(guests)} guests: no guest names ({g})")
        if f"guest:{g}" not in w.names:
            w.names.append(f"guest:{g}")
    if w.update and w.stale:
        raise Refused("GuestTest2 update is its own selection: 'stale' adds nothing to it")


def _img(w, args, known):
    plats = tuple(known.get("img") or PLATS)
    for tok in args:
        if tok.lower() == "release":
            w.release = True
            continue
        if _keyword(w, tok):
            continue
        p = tok[4:] if tok.startswith("img:") else tok
        if p not in plats:
            raise Refused(f"no image {p!r}: {' '.join(plats)}")
        if w.all:
            raise Refused(f"ImgTest2All tests every image: no platform names ({p})")
        if f"img:{p}" not in w.names:
            w.names.append(f"img:{p}")


def _composite(w, args, known):
    """BkoTest2 and SweepTest2 take a round and plan (and BkoTest2 stale), nothing else."""
    me = COMPOSITE_WORD[w.family]
    if w.family == "sweep":
        w.sweep = True
    h = known.get("holt") or {}
    gate = [str(x) for x in known.get("gate") or ()]
    named = set(known.get("stic") or ()) | set(h.get("sources") or ()) | set(known.get("guest") or ()) \
        | set(known.get("img") or PLATS) | set(gate) | {x.split("@")[0] for x in gate} | {x.split(":")[0] for x in gate}
    go = "SticTest2, HoltTest2, GuestTest2, ImgTest2 or GateTest2 for named cells"
    if w.all:
        raise Refused(f"{me}All isn't a word: {me} has no All form (SweepTest2 runs every All form; {go})"
                      if w.family == "bko" else f"{me} is every All form already: no {me}All")
    for tok in args:
        if tok.lower() == "plan":
            w.plan = True
        elif tok.lower() == "stale":
            if w.family == "sweep":
                raise Refused("SweepTest2 runs everything fresh: 'stale' adds nothing to it")
            w.stale = True
        elif tok.lower() == "all":
            raise Refused(f"{me} has no All form: SweepTest2 runs every All form" if w.family == "bko"
                          else "SweepTest2 is every All form already")
        elif ":" in tok or "+" in tok or tok in named or tok.lower() in _LETTER:
            raise Refused(f"{me} takes no names ({tok}): {go}" + ("" if w.family == "sweep" else ", SweepTest2 for all of them"))
        else:
            _round(w, tok, f"{me} argument")


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
