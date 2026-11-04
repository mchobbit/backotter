#!/usr/bin/env python3
"""Per-test results of a restic, rustic_core or rustic run, and the
comparison of a fork's against stock's (testvm/audit/README.md).

Usage:
  results.py go OUT        parse OUT/test.json (go test -json), vet.txt,
                           cross/ and steps.tsv into OUT/results.tsv,
                           details.json and summary.txt (audit-host.sh)
  results.py cargo OUT     the same from OUT/test.log and doctest.log
                           (cargo test's text output)
  results.py summary OUT   print OUT/summary.txt's counts line
  results.py compare STOCK FORK
                           compare two OUT directories; print the fork's
                           failures stock doesn't have, its skips of tests
                           stock passes, tests missing on it, tests it added,
                           build rows that fail, and stock's known failures;
                           exit 1 if the fork fails anything stock doesn't

results.tsv rows are SUITE<TAB>TEST<TAB>STATUS, STATUS pass, fail or skip
(cargo's "ignored" is skip).  A doc test's "(line N)" is left out of its
name (a duplicate gets " #2"), and a go vet finding's line and column, so
that lines moved by the fork's changes still match.  Comparing, a test that
is only on one side but whose name with its digits replaced (and Go's "#01"
of a repeated name left out) matches one on the other (Go subtests named
after random sizes, TestList/max-1148) counts as the same test.

Python 3.6 or later; nothing outside the standard library.
"""
import collections
import json
import os
import re
import sys

ORDER = {"fail": 0, "skip": 1, "pass": 2}


def worst(a, b):
    if a is None:
        return b
    return a if ORDER[a] <= ORDER[b] else b


def write(out, rows, details):
    rows = sorted(rows.items())
    with open(os.path.join(out, "results.tsv"), "w") as f:
        for (suite, test), st in rows:
            f.write("%s\t%s\t%s\n" % (suite, test, st))
    with open(os.path.join(out, "details.json"), "w") as f:
        json.dump(details, f, indent=0, sort_keys=True)
    c = counts(dict(rows))
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(c + "\n")


def counts(rows):
    t = collections.Counter(st for (s, n), st in rows.items() if not s.startswith("#") and n != "(package)")
    # Builds: #build, #cross, #bin rows and go vet as a whole, not its findings.
    b = collections.Counter(st for (s, n), st in rows.items()
                            if s in ("#build", "#cross", "#bin") or (s, n) == ("#vet", "(go vet)"))
    return "tests: %d pass, %d fail, %d skip; builds: %d pass, %d fail" % (
        t["pass"], t["fail"], t["skip"], b["pass"], b["fail"])


def steps(out):
    res = []
    try:
        for line in open(os.path.join(out, "steps.tsv")):
            p = line.rstrip("\n").split("\t")
            if len(p) == 3:
                res.append((p[0], int(p[1]), int(p[2])))
    except OSError:
        pass
    return res


def tail(text, n=40, head=0):
    """The last n lines of text, after its first head lines when it has more."""
    lines = text.rstrip("\n").split("\n")
    if head and len(lines) > head + n:
        return "\n".join(lines[:head] + ["[...]"] + lines[-n:])
    return "\n".join(lines[-n:])


def parse_go(out):
    rows, details = {}, {}
    output = collections.defaultdict(list)
    buildout = collections.defaultdict(list)
    for line in open(os.path.join(out, "test.json"), errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        a = e.get("Action")
        if a == "build-output":
            buildout[e.get("ImportPath", "")].append(e.get("Output", ""))
            continue
        if a == "build-fail":
            k = ("#build", "go test " + e.get("ImportPath", ""))
            rows[k] = "fail"
            details[k[0] + "\t" + k[1]] = tail("".join(buildout[e.get("ImportPath", "")]))
            continue
        pkg = e.get("Package")
        if not pkg:
            continue
        k = (pkg, e.get("Test") or "(package)")
        if a == "output":
            output[k].append(e.get("Output", ""))
        elif a in ("pass", "fail", "skip"):
            rows[k] = a
    for k, st in rows.items():
        if st == "fail" and k[0] != "#build":
            details[k[0] + "\t" + k[1]] = tail("".join(output[k]))
    # A test that started but never ended (the package's binary died or
    # timed out) counts as failed.
    for k in output:
        if k not in rows and k[1] != "(package)":
            rows[k] = "fail"
            details[k[0] + "\t" + k[1]] = tail("".join(output[k]) + "\n(no result: the test binary ended first)")
    # go vet: one row overall, one per finding without its line and column.
    st = dict((label, rc) for label, rc, _ in steps(out))
    if "go vet" in st:
        rows[("#vet", "(go vet)")] = "pass" if st["go vet"] == 0 else "fail"
        try:
            vet = open(os.path.join(out, "vet.txt"), errors="replace").read()
        except OSError:
            vet = ""
        if st["go vet"] != 0:
            details["#vet\t(go vet)"] = tail(vet, 60)
        for line in vet.split("\n"):
            m = re.match(r"^(?:vet: )?(\S+?\.go):\d+(?::\d+)?: (.*)$", line)
            if m:
                k = ("#vet", "%s: %s" % (m.group(1), m.group(2)))
                rows[k] = "fail"
                details[k[0] + "\t" + k[1]] = line
    for label, rc, _ in steps(out):
        if label.startswith("cross "):
            k = ("#cross", label[6:])
            rows[k] = "pass" if rc == 0 else "fail"
            if rc:
                p = os.path.join(out, "cross", label[6:].replace("/", "-") + ".txt")
                details[k[0] + "\t" + k[1]] = tail(open(p, errors="replace").read())
        elif label == "go build":
            rows[("#build", "go build ./cmd/restic")] = "pass" if rc == 0 else "fail"
            if rc:
                details["#build\tgo build ./cmd/restic"] = tail(open(os.path.join(out, "build.log"), errors="replace").read())
    write(out, rows, details)


RUNNING = re.compile(r"^\s+Running (?:unittests )?(\S+) \((.+)\)\s*$")
DOCTESTS = re.compile(r"^\s+Doc-tests (\S+)\s*$")
TESTLINE = re.compile(r"^test (.+?) \.\.\. (ok|FAILED|ignored(?:, .*)?)\s*$")
RESULT = re.compile(r"^test result: ")
NOCOMPILE = re.compile(r"^error: could not compile `([^`]+)`(?: \(([^)]*)\))?")


def parse_cargo(out):
    rows, details = {}, {}
    for logname in ("test.log", "doctest.log"):
        p = os.path.join(out, logname)
        if not os.path.exists(p):
            continue
        text = open(p, errors="replace").read()
        lines = text.split("\n")
        suite, seen_result, doc = None, True, False
        nocompile = False
        dup = collections.Counter()
        failout = {}

        def close():
            if suite is not None and not seen_result:
                k = ("#bin", suite)
                rows[k] = "fail"
                details["#bin\t" + suite] = "(the test binary ended without its \"test result\" line)"

        for i, line in enumerate(lines):
            m = RUNNING.match(line)
            if m:
                close()
                binname = re.sub(r"-[0-9a-f]{16}$", "", os.path.basename(m.group(2)))
                suite, seen_result, doc = "%s [%s]" % (m.group(1), binname), False, False
                continue
            m = DOCTESTS.match(line)
            if m:
                close()
                suite, seen_result, doc = "doc-tests " + m.group(1), False, True
                dup = collections.Counter()
                continue
            if RESULT.match(line):
                seen_result = True
                continue
            m = NOCOMPILE.match(line)
            if m:
                k = ("#build", "%s (%s)" % (m.group(1), m.group(2) or "lib"))
                rows[k] = "fail"
                nocompile = True
                start = max(0, i - 60)
                details["#build\t" + k[1]] = "\n".join(l for l in lines[start:i + 1] if l.strip())[-6000:]
                continue
            m = TESTLINE.match(line)
            if m and suite is not None:
                name = m.group(1)
                if doc:
                    name = re.sub(r" \(line \d+\)", "", name)
                    dup[name] += 1
                    if dup[name] > 1:
                        name = "%s #%d" % (name, dup[name])
                st = {"ok": "pass", "FAILED": "fail"}.get(m.group(2), "skip")
                rows[(suite, name)] = st
                if st == "fail":
                    failout[m.group(1)] = (suite, name)
        close()
        # The step as a build: every crate it needed compiled, or not.
        k = ("#build", "cargo test" + (" --doc" if logname == "doctest.log" else ""))
        rows[k] = "fail" if nocompile else "pass"
        # "---- NAME stdout ----" blocks hold what the failed tests printed.
        blocks = re.split(r"^---- (.+?) stdout ----$", text, flags=re.M)
        for j in range(1, len(blocks) - 1, 2):
            k = failout.get(blocks[j])
            if k:
                body = re.split(r"^(?:failures:|successes:)\s*$", blocks[j + 1], flags=re.M)[0]
                details[k[0] + "\t" + k[1]] = tail(body, 30, 6)
        for k in failout.values():
            details.setdefault(k[0] + "\t" + k[1], "(no output captured)")
    for label, rc, _ in steps(out):
        if label == "prep-rustic-src":
            rows[("#build", "prep-rustic-src.sh")] = "pass" if rc == 0 else "fail"
    write(out, rows, details)


def load(d):
    rows = {}
    for line in open(os.path.join(d, "results.tsv"), errors="replace"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 3:
            rows[(p[0], p[1])] = p[2]
    try:
        details = json.load(open(os.path.join(d, "details.json")))
    except (OSError, ValueError):
        details = {}
    return rows, details


def info(d):
    res = {}
    try:
        for line in open(os.path.join(d, "info.txt")):
            k, _, v = line.rstrip("\n").partition("=")
            res.setdefault(k, v)
    except OSError:
        pass
    return res


def fuzzy(k):
    # Go's "#01" suffix of a repeated subtest name goes too.
    return (k[0], re.sub(r"\d+", "#", re.sub(r"#\d+$", "", k[1])))


def compare(sd, fd):
    s, sdet = load(sd)
    f, fdet = load(fd)
    si, fi = info(sd), info(fd)
    print("stock: %s (%s, %s)" % (si.get("commit", "?"), si.get("host", "?"), si.get("date", "?")))
    print("  %s" % counts(s))
    print("fork:  %s (%s, %s)" % (fi.get("commit", "?"), fi.get("host", "?"), fi.get("date", "?")))
    print("  %s" % counts(f))
    for k in ("go", "rustc", "rustic_core_used", "walkdir_used"):
        if k in si or k in fi:
            print("  %s: stock %s; fork %s" % (k, si.get(k, "-"), fi.get(k, "-")))
    sfz = collections.defaultdict(list)
    ffz = collections.defaultdict(list)
    for k in s:
        sfz[fuzzy(k)].append(k)
    for k in f:
        ffz[fuzzy(k)].append(k)

    def stock_of(k):
        if k in s:
            return s[k]
        st = None
        for o in sfz.get(fuzzy(k), []):
            st = worst(st, s[o])
        return st

    def show(k, det):
        txt = det.get(k[0] + "\t" + k[1])
        if txt:
            for l in txt.rstrip("\n").split("\n"):
                print("      | " + l)

    tests = lambda k: not k[0].startswith("#")
    builds = lambda k: k[0].startswith("#")
    fails = [k for k in sorted(f) if f[k] == "fail" and stock_of(k) != "fail"]
    tfails = [k for k in fails if tests(k)]
    bfails = [k for k in fails if builds(k)]
    print("\nFork-only test failures (fail on the fork, not on stock): %d" % len(tfails))
    for k in tfails:
        print("  %s :: %s  (stock: %s)" % (k[0], k[1], stock_of(k) or "absent"))
        show(k, fdet)
    print("\nBuild targets that fail on the fork, not on stock: %d" % len(bfails))
    for k in bfails:
        print("  %s %s  (stock: %s)" % (k[0], k[1], stock_of(k) or "absent"))
        show(k, fdet)
    allb = sorted(k for k in f if builds(k) and k[1] != "(go vet)" and k[0] != "#vet")
    if allb:
        print("  (all builds on the fork: %s)" % ", ".join(
            "%s %s" % (k[1], f[k]) for k in allb))
    skips = [k for k in sorted(f) if f[k] == "skip" and stock_of(k) == "pass" and tests(k)]
    print("\nSkipped on the fork, passing on stock: %d" % len(skips))
    for k in skips:
        print("  %s :: %s" % k)
    missing = [k for k in sorted(s) if k not in f and fuzzy(k) not in ffz and tests(k)]
    print("\nOn stock, missing on the fork: %d" % len(missing))
    for k in missing:
        print("  %s :: %s  (stock: %s)" % (k[0], k[1], s[k]))
    added = [k for k in sorted(f) if k not in s and fuzzy(k) not in sfz and tests(k)]
    c = collections.Counter(f[k] for k in added)
    print("\nAdded by the fork: %d (%d pass, %d fail, %d skip)" % (len(added), c["pass"], c["fail"], c["skip"]))
    for k in added:
        print("  %s :: %s  %s" % (k[0], k[1], f[k]))
    known = [k for k in sorted(s) if s[k] == "fail"]
    print("\nKnown stock failures: %d" % len(known))
    for k in known:
        print("  %s :: %s  (fork: %s)" % (k[0], k[1], f.get(k) or "absent"))
    return 1 if fails else 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "go":
        parse_go(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "cargo":
        parse_cargo(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "summary":
        print(counts(load(sys.argv[2])[0]))
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__.strip().split("\n\n")[1])


if __name__ == "__main__":
    main()
