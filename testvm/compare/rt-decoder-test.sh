#!/usr/bin/env bash
# rt-common.sh's tree decoder (the awk rt_nodes runs on each tree's JSON) on
# made-up trees, in every awk here: its output, and the next level's queue,
# must be the same as the decoder's with the whole tree in its window (how it
# read a tree before the window, rt-common.sh "Tokens are cut off..."), and
# with chunks of 16 bytes (every token across a chunk's end).
#
# Usage: testvm/compare/rt-decoder-test.sh [AWK...]   (default: each of
#        gawk mawk nawk "busybox awk" awk that is here; python3 makes the trees)
# Prints a line per awk, locale and mode; exits 1 if any differs (an awk
# that fails on the input must fail in every mode).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/rt-decoder-test.XXXXXX") || exit 2
trap 'rm -rf "$T"' EXIT
awk '/^cat >\$rt_decoder <<.AWK.$/ { f = 1; next } /^AWK$/ { f = 0 } f' "$HERE/rt-common.sh" >"$T/dec.awk"
grep -q 'h <= 1024' "$T/dec.awk" || { echo "rt-decoder-test: no chunk size in the decoder" >&2; exit 2; }
sed 's/h <= 1024/h <= 1e15/' "$T/dec.awk" >"$T/whole.awk"
sed 's/h <= 1024/h <= 16/' "$T/dec.awk" >"$T/c16.awk"

# Walking: trees after "\001ID<TAB>PREFIX" lines; long strings and xattr
# values (past a chunk), escapes, multibyte and non-UTF-8 names, whitespace
# runs and line breaks between tokens, nodes: null, an empty tree.
python3 -I - "$T/walk.in" "$T/one.in" <<'PY'
import json, random, sys
random.seed(20261118)
POOL = ['a', 'Z', '0', ' ', '\\"', '\\\\', '\\n', '\\u00e9', 'é', '日本', '\\t', ',', ':', '{', '}', '[', ']', '/']
def rstr(n):
    return ''.join(random.choice(POOL) for _ in range(n))
def tree(nn):
    nodes = []
    for _ in range(nn):
        n = {"name": rstr(random.randint(1, 40)), "type": random.choice(["file", "dir", "symlink"]),
             "mode": random.choice([0, 420, 2147484141]), "size": random.choice([0, 7, 123456789012]),
             "mtime": "2026-11-18T21:%02d:%02d.%09d%s" % (random.randint(0, 59), random.randint(0, 59),
                                                         random.randint(0, 999999999), random.choice(["Z", "+01:00", "-05:30"])),
             "uid": random.randint(0, 70000), "content": None if random.random() < .5 else ["%064x" % random.getrandbits(256)],
             "extended_attributes": [{"name": "user.%s" % rstr(5), "value": rstr(random.choice([1, 300, 2500]))}
                                     for _ in range(random.choice([0, 0, 0, 2]))]}
        if n["type"] == "dir":
            n["subtree"] = "%064x" % random.getrandbits(256)
        nodes.append(n)
    b = json.dumps({"nodes": nodes}, separators=(",", ":"), ensure_ascii=False, sort_keys=random.random() < .5)
    if random.random() < .4:
        b = b.replace(',"', ',   \n  "').replace(':[', ' :\t[ ')
    b = b.encode()
    if random.random() < .4:
        b = b.replace(b'"name":"a', b'"name":"\xff\xfea', 2)
    return b
with open(sys.argv[1], "wb") as f:
    for t, nn in enumerate([0, 1, 3, 40, 200, 2, 120, 7, 60, 1]):
        f.write(b"\x01%064x\tp%d/\n" % (random.getrandbits(256), t) + tree(nn) + b"\n\n")
    f.write(b"\x01%064x\tnull/\n" % random.getrandbits(256) + b'{"nodes":null}\n')
with open(sys.argv[2], "wb") as f:
    f.write(tree(150) + b"\n")
PY
[ -s "$T/walk.in" ] || { echo "rt-decoder-test: python3 made no trees" >&2; exit 2; }

if [ $# -gt 0 ]; then awks=("$@"); else
    awks=()
    for a in gawk mawk nawk "busybox awk" awk; do
        # shellcheck disable=SC2086
        $a 'BEGIN { exit 0 }' </dev/null >/dev/null 2>&1 && awks+=("$a")
    done
fi
[ ${#awks[@]} -gt 0 ] || { echo "rt-decoder-test: no awk here" >&2; exit 2; }
bad=0
for a in "${awks[@]}"; do
    for loc in C en_US.UTF-8 C.UTF-8; do
        for mode in walk one; do
            for v in whole dec c16; do
                rm -f "$T/q.$v"
                # shellcheck disable=SC2086
                LC_ALL=$loc $a -v walk=$([ $mode = walk ] && echo 1) -v prune= -v queue="$T/q.$v" -v badfile="$T/b.$v" \
                    -f "$T/$v.awk" <"$T/$mode.in" >"$T/o.$v" 2>/dev/null
                echo "rc=$?" >"$T/rc.$v"; cat "$T/rc.$v" >>"$T/o.$v"
                [ -f "$T/q.$v" ] && cat "$T/q.$v" >>"$T/o.$v"
            done
            # (An awk that fails on the input, as macOS's awk on non-UTF-8 bytes in
            # a UTF-8 locale, fails in every mode, after output that differs: rt_walk
            # takes a failed decode as "can't read tree", whatever came before it.)
            if cmp -s "$T/o.whole" "$T/o.dec" && cmp -s "$T/o.whole" "$T/o.c16"; then r=PASS
            elif ! grep -qx 'rc=0' "$T/rc.whole" && ! grep -qx 'rc=0' "$T/rc.dec" && ! grep -qx 'rc=0' "$T/rc.c16"; then
                r="PASS (fails in each)"
            else r=FAIL; bad=1; fi
            echo "$r $a LC_ALL=$loc $mode ($(grep -c . "$T/o.whole") lines, $(cat "$T/rc.whole"))"
        done
    done
done
exit $bad
