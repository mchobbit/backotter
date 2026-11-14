"""The families registry: the one table (docs/design/bs2-all.md, "The family
interface (A1)").

Every module in this package (pkgutil, sorted by file name; base.py and
names starting with "_" aside) is imported, and its FAMILY (a Family, or
None) and TRAITS (a list of Trait) collected.  Adding a module adds it to
the table: there is no list to edit.  The registry refuses, at import:
- two modules (families or traits) of the same name;
- an edit op taken twice (by two modules, or by a module and edits.OPS),
  and a draw without its EditOp;
- rule 2 rows for one tool in two traits, or in a trait and a family (they
  could both match one source).  Two families may each have rows for one
  tool: a source has only one family.
A unit test (tests/test_families.py) checks that exactly one family is the
most specific match for every corpus dump of a built OS.

- family_of(source, dump): the matching family with the highest priority
  (generic Linux has the lowest), else None.  Two at the same top priority
  is an error.
- hooks(source, dump): a Hooks, the family's hooks merged with those of
  every trait that matches.  The generator, the rules, the tables, rule 3's
  texts, the sweep and tier 0 call only this; generate() makes it once per
  case.
- PLANNED: what isn't built, by the dump's OS (os.family, or os.id for
  Sculpt OS), with the piece that builds it.  skipped(source, dump) names
  that piece for a source with no family (A9's SKIPPED).  A lane that adds
  its family drops its row here in the same commit.
"""

import importlib
import pkgutil
from dataclasses import dataclass, field

from .. import tables as T
from .base import Family, Module, Quirk, Trait  # noqa: F401  (the interface, re-exported)

PLANNED = {
    "freebsd": "A4", "openbsd": "A4", "netbsd": "A4", "dragonfly": "A4",
    "illumos": "A5",
    "haiku": "A6", "redox": "A6", "sculpt": "A6",
}


class RegistryError(Exception):
    pass


@dataclass
class Registry:
    families: list = field(default_factory=list)
    traits: list = field(default_factory=list)
    edits: dict = field(default_factory=dict)
    recipes: dict = field(default_factory=dict)
    fs_can: dict = field(default_factory=dict)


def build(modules, generic_ops=None):
    """The registry of MODULES ([(module name, module)]); RegistryError on a conflict."""
    if generic_ops is None:
        from .. import edits as E
        generic_ops = set(E.OPS)
    reg = Registry()
    names = {}
    ops = {}
    for modname, m in modules:
        fam = getattr(m, "FAMILY", None)
        trs = list(getattr(m, "TRAITS", None) or [])
        for x in ([fam] if fam else []) + trs:
            if not isinstance(x, Module):
                raise RegistryError(f"{modname}: {x!r} isn't a Family or a Trait")
            if isinstance(x, Family) is (x in trs):
                raise RegistryError(f"{modname}: {x.name} is in the wrong list (FAMILY takes a Family, TRAITS Traits)")
            if x.name in names:
                raise RegistryError(f"two modules named {x.name!r}: {names[x.name]} and {modname}")
            names[x.name] = modname
            for op in set(x.draws) | set(x.edits):
                if op in generic_ops:
                    raise RegistryError(f"{x.name}: edit op {op!r} is already edits.OPS's")
                if op in ops:
                    raise RegistryError(f"edit op {op!r} is taken twice: by {ops[op]} and {x.name}")
                ops[op] = x.name
            for op in x.draws:
                if op not in x.edits:
                    raise RegistryError(f"{x.name}: draw {op!r} has no EditOp in its edits")
            reg.edits.update(x.edits)
            for k, v in x.recipes.items():
                if k in reg.recipes:
                    raise RegistryError(f"content recipe {k!r} is taken twice")
                reg.recipes[k] = v
            for k, v in x.fs_can.items():
                reg.fs_can.setdefault(k, set()).update(v)
        if fam:
            reg.families.append(fam)
        reg.traits += trs
    # Rule 2 rows: a tool's rows in one trait only, and not in a family too.
    by_tool = {}
    for t in reg.traits:
        for tool in {r[0] for r in t.tables}:
            if tool in by_tool:
                raise RegistryError(f"rule 2 rows for {tool} in two traits: {by_tool[tool]} and {t.name}")
            by_tool[tool] = t.name
    for f in reg.families:
        for tool in {r[0] for r in f.tables}:
            if tool in by_tool:
                raise RegistryError(f"rule 2 rows for {tool} in trait {by_tool[tool]} and family {f.name}")
    return reg


def discover():
    out = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name == "base" or info.name.startswith("_"):
            continue
        out.append((info.name, importlib.import_module(f"{__name__}.{info.name}")))
    return out


REGISTRY = build(discover())


# ---- a source's hooks -----------------------------------------------------------

@dataclass
class Hooks:
    """A source's family and traits, merged: what the shared code reads."""
    family: object = None
    traits: tuple = ()
    tables: list = field(default_factory=lambda: list(T.TABLES))
    packages: dict = field(default_factory=lambda: dict(T.PACKAGES))
    versions: dict = field(default_factory=dict)
    quirks: tuple = ()
    ops: tuple = ()
    draws: dict = field(default_factory=dict)
    edits: dict = field(default_factory=dict)
    texts: tuple = ()
    patterns: tuple = ()
    finders: tuple = ()
    content: object = None
    known: dict = field(default_factory=dict)
    steers: tuple = ()
    sweeps: tuple = ()
    tier0: str = "chroot"

    @property
    def names(self):
        return ([self.family.name] if self.family else []) + [t.name for t in self.traits]

    def allowed(self, dump, tool, fieldname):
        return T.allowed(dump, tool, fieldname, self)

    def weights(self, dump, weights):
        for f in self.ops:
            weights = f(dump, weights)
        return weights

    def quirk_check(self, source, drifted, edits):
        out = []
        for q in self.quirks:
            out += q.check(source, drifted, edits)
        return out

    def config_texts(self, dump):
        out = []
        for f in self.texts:
            out += [str(x) for x in f(dump)]
        return out

    def number_patterns(self, dump, dk, p):
        out = []
        for f in self.patterns:
            out += f(dump, dk, p)
        return out

    def names_by_number(self, dump, dk, p):
        out = []
        for f in self.finders:
            out += f(dump, dk, p)
        return out

    def recipe(self, dump):
        """The content recipe this source draws (the family's), or None."""
        if self.content is None:
            return None
        from .. import content as C, dumpx
        ms = sorted((m for m in dumpx.storage(dump).get("mounts") or [] if (C.DIR + "/").startswith(str(m.get("target") or "").rstrip("/") + "/")),
                    key=lambda m: len(m["target"]))
        return self.content(dump, ms[-1].get("fstype") if ms else None)

    def steer(self, dump, drifted, edits, opts):
        for f in self.steers:
            opts = f(dump, drifted, edits, opts)
        return opts

    def sweep(self, dump, opts):
        for f in self.sweeps:
            opts = f(dump, opts)
        return opts


def _matching(reg, source, dump):
    fams = [f for f in reg.families if f.matches(source, dump)]
    trs = [t for t in reg.traits if t.matches(source, dump)]
    if not fams:
        return None, trs
    top = max(f.priority for f in fams)
    best = [f for f in fams if f.priority == top]
    if len(best) > 1:
        raise RegistryError(f"{source or 'the source'}: families {', '.join(f.name for f in best)} all match at priority {top}")
    return best[0], trs


def family_of(source, dump, reg=None):
    return _matching(reg or REGISTRY, source, dump)[0]


def traits_of(source, dump, reg=None):
    return _matching(reg or REGISTRY, source, dump)[1]


def merge(fam, traits):
    """The Hooks of FAM and TRAITS."""
    mods = ([fam] if fam else []) + list(traits)
    rows = {}
    for m in mods:
        for tool in {r[0] for r in m.tables}:
            if tool in rows:
                raise RegistryError(f"rule 2 rows for {tool} in {rows[tool]} and {m.name}")
            rows[tool] = m.name
    tabs = [r for r in T.TABLES if r[0] not in rows] + [r for m in mods for r in m.tables]
    pk = dict(T.PACKAGES)
    for m in mods:
        for tool, names in m.packages.items():
            pk[tool] = tuple(pk.get(tool, ())) + tuple(n for n in names if n not in pk.get(tool, ()))
    known = {}
    for m in mods:
        known.update(m.known)
    draws, edits = {}, {}
    for m in mods:
        draws.update(m.draws)
        edits.update(m.edits)
    return Hooks(
        family=fam, traits=tuple(traits), tables=tabs, packages=pk,
        quirks=tuple(q for m in mods for q in m.quirks),
        ops=tuple(m.ops for m in mods if m.ops),
        draws=draws, edits=edits,
        texts=tuple(m.config_texts for m in mods if m.config_texts),
        patterns=tuple(m.number_patterns for m in mods if m.number_patterns),
        finders=tuple(m.names_by_number for m in mods if m.names_by_number),
        content=fam.content if fam else None,
        known=known,
        steers=tuple(m.steer for m in mods if m.steer),
        sweeps=tuple(m.sweep for m in mods if m.sweep),
        tier0=fam.tier0 if fam else "plan",
    )


def hooks(source, dump, reg=None):
    """SOURCE's (a name, or None) hooks, from its DUMP."""
    fam, trs = _matching(reg or REGISTRY, source, dump)
    h = merge(fam, trs)
    if fam and fam.versions:
        h.versions = dict(fam.versions(dump) or {})
    return h


def os_key(dump):
    o = dump.get("os") or {}
    return o.get("family") or "linux"


def skipped(source, dump, reg=None):
    """The unbuilt piece (PLANNED) for a source with no family, else None."""
    if family_of(source, dump, reg):
        return None
    o = dump.get("os") or {}
    return PLANNED.get(o.get("family")) or PLANNED.get(o.get("id")) or "unplanned"


def edit_op(name):
    """A module's EditOp by its op name (edits.apply's fallback)."""
    return REGISTRY.edits.get(name)


def recipe(name):
    return REGISTRY.recipes.get(name)


def fs_can(fs_type):
    return REGISTRY.fs_can.get(fs_type)
