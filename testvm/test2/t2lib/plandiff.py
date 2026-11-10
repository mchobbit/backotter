#!/usr/bin/env python3
"""plandiff.py: which stages of a bko-holt load plan changed.

A golden plan (holt/tests/corpus/GUEST/plan.txt, made by
holt/tests/golden_plans.rs) is load's standard output, then golden_plans.rs's
own lines (exit status, standard error).  Its sections:

  header    everything before the first stage header: the source, the disk
            mapping, Problems and Notes
  STAGE     each `# ---- STAGE ----` header and what follows it: partition,
            assemble, format, mount, restore, boot, close
  trailer   from the first line after the last stage header that starts
            "# Nothing was written" (plan.rs's last line) or is golden_plans.rs's
            own ("# golden_plans.rs:"), to the end; with no stage at all,
            from the first such line

holt/tests/common/mod.rs splits plans the same way for diff.json, whose
"stages" lists are what stages() returns.  HoltTest2 adds a guest's variants
when its plan diff touches partition, assemble or close (VARIANT_STAGES).

    plandiff.py OLD NEW [--json]    the changed sections, one a line, then
                                    the lines changed; exit 0 same, 1 differ,
                                    2 usage

Standard library only; Python 3.9 or later.
"""

import json
import sys

STAGES = ("partition", "assemble", "format", "mount", "restore", "boot", "close")
VARIANT_STAGES = ("partition", "assemble", "close")
GOLDEN_MARK = "# golden_plans.rs:"
TRAILER_MARKS = ("# Nothing was written", GOLDEN_MARK)
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def _lines(text):
    """TEXT's lines as Rust's str::lines makes them: no last empty line."""
    if not text:
        return []
    parts = text.split("\n")
    if text.endswith("\n"):
        parts.pop()
    return [p[:-1] if p.endswith("\r") else p for p in parts]


def stage_header(line):
    """The stage a `# ---- STAGE ----` line starts, else None."""
    if not (line.startswith("# ---- ") and line.endswith(" ----")):
        return None
    name = line[len("# ---- "):-len(" ----")]
    if name and all(c in _NAME_CHARS for c in name):
        return name
    return None


def sections(text):
    """TEXT as [(name, [lines])]: header, the stages in their order, trailer."""
    lines = _lines(text)
    last_stage = None
    for i, l in enumerate(lines):
        if stage_header(l) is not None:
            last_stage = i
    start = 0 if last_stage is None else last_stage + 1
    trailer = None
    for i in range(start, len(lines)):
        if lines[i].startswith(TRAILER_MARKS):
            trailer = i
            break
    out = [("header", [])]
    for i, l in enumerate(lines):
        if i == trailer:
            out.append(("trailer", []))
        else:
            name = stage_header(l)
            if name is not None and (trailer is None or i < trailer):
                out.append((name, []))
        out[-1][1].append(l)
    return out


def _joined(secs, name):
    parts = [l for n, l in secs if n == name]
    if not parts:
        return None
    return [x for p in parts for x in p]


def stages(old_text, new_text):
    """The sections whose lines differ between OLD_TEXT and NEW_TEXT (a
    section in only one of them counts), in plan order: header, STAGES,
    any other stage as it first appears, trailer."""
    a, b = sections(old_text), sections(new_text)
    names = ["header"] + list(STAGES)
    for n, _ in a + b:
        if n not in names and n != "trailer":
            names.append(n)
    names.append("trailer")
    return [n for n in names if _joined(a, n) != _joined(b, n)]


def lines_changed(old_text, new_text):
    """Lines removed plus lines added by a line diff that keeps a longest
    common subsequence (as golden_plans.rs counts them)."""
    a, b = _lines(old_text), _lines(new_text)
    pre = 0
    while pre < len(a) and pre < len(b) and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < len(a) - pre and suf < len(b) - pre and a[len(a) - 1 - suf] == b[len(b) - 1 - suf]:
        suf += 1
    a, b = a[pre:len(a) - suf], b[pre:len(b) - suf]
    if not a or not b:
        return len(a) + len(b)
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b):
            cur[j + 1] = prev[j] + 1 if x == y else max(prev[j + 1], cur[j])
        prev = cur
    return len(a) + len(b) - 2 * prev[len(b)]


def needs_variants(changed):
    """Whether CHANGED (stages()) touches a stage HoltTest2 reruns a guest's
    variants for."""
    return any(s in VARIANT_STAGES for s in changed)


def main(argv):
    args = [a for a in argv if a != "--json"]
    if len(args) != 2 or args[0].startswith("-") or args[1].startswith("-"):
        print("usage: plandiff.py OLD NEW [--json]", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8", errors="replace") as f:
            old = f.read()
        with open(args[1], encoding="utf-8", errors="replace") as f:
            new = f.read()
    except OSError as e:
        print("plandiff.py: %s" % e, file=sys.stderr)
        return 2
    changed = stages(old, new)
    n = lines_changed(old, new)
    if "--json" in argv:
        print(json.dumps({"stages": changed, "lines_changed": n, "variants": needs_variants(changed)}))
    else:
        for s in changed:
            print(s)
        print("%d lines changed" % n)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
