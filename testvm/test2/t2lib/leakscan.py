#!/usr/bin/env python3
"""leakscan.py: what must not reach the public repository, found in files.

H1's dump corpus (holt/tests/corpus) and Test2's fixtures are scanned before
they enter the repository; nothing found may stay.  Two parts:

(a) Patterns, in every string value and object key of a JSON file (and every
    blob in it: `_hex` and `first_bytes` values decoded from hex, `b64` and
    `_b64` ones from base64, each read as latin-1 and as UTF-16LE at both byte
    alignments), in every line of any other file, and in a symlink's target
    (a symlink is never followed):
      mac      six hex pairs joined by : or by - (not glued to a hex digit, and
               not part of a longer run of pairs with the same separator), and
               MAC: or MAC( with 12 hex digits; not redact.rs's pseudonyms (bc,
               six or more zeros, a counter of 1 to 4 hex digits:
               bc0000000001, in any of its separator forms; redact.rs:876-885
               pads its counter so), nor the all-zero and broadcast MACs, which
               redact.rs keeps as they are since they identify nothing
               (redact.rs:475)
      ip       private and link-local addresses: 10/8, 172.16/12, 192.168/16,
               169.254/16, fe80::/10, fc00::/7
      email    an e-mail address
      key      a private key (BEGIN ... PRIVATE KEY) or an SSH public key
      host     the test hosts' names, chopin, cinqcent, copland and cage (cage as a
               word only); allowed in index.tsv
               and README.md, and anywhere with --allow-hosts (fixtures)
(b) Strings, with --strings FILE (JSON lines {"k": KIND, "v": VALUE}, made by
    `leakscan.py strings` on each host and never moved off it) and --git-email
    REPO: each found case-insensitively in text, and in blobs as ASCII,
    UTF-16LE and, for a MAC, its 6 raw bytes.  With --raw DIR, a scanned
    GUEST/src.json also has the identifiers of DIR/GUEST/src.json (its raw
    dump: serials, WWIDs, UUIDs and GUIDs, machine-ids, MACs) searched in it,
    raw UUIDs and MACs as bytes too.  Values under 8 bytes, loopback and
    link-local addresses are left out.  Every scanned GUEST/src.json must
    have its raw twin, and it must parse.

A finding names the file, where in it (a JSON path or a line) and the class,
never the text: at most its shape ("12 hex", "IPv4 private").  A scan that
couldn't do all it was asked is an error, exit 2, whatever it found: a file
it can't read (or that isn't a file, a directory or a symlink), a scanned
src.json without a raw twin that parses, a --git-email repository whose git
gives no address, a --strings line that isn't a {"k", "v"} object.  Errors
name the file and line, never what's in them.

    leakscan.py scan [--allow-hosts] [--strings FILE] [--git-email REPO]
                     [--raw DIR] PATH...
        PATHs are files or directories (searched for every file but
        __pycache__, *.pyc, .DS_Store; symlinks are scanned as their
        target's name); exit 0 clean, 1 findings, 2 usage or an incomplete
        scan
    leakscan.py strings [--vms DIR]
        this host's strings as JSON lines on stdout: the contents of
        DIR/*/{console-passphrase,luks-passphrase,zfs-passphrase,root-password}
        (DIR: ~/backotter-work/vms), disk serials and WWIDs (sysfs and
        /dev/disk/by-id), its IPv4 and IPv6 addresses, its MACs, and git's
        user.email.  Redirect it into a file of mode 0600, and delete it after
        the scan.

As a module: scan_paths() and scan_file() give Finding objects (and raise
ScanError, or collect its messages in an ERRORS list);
pattern_findings() and pattern_spans() run (a) on one text, and
scrub_text() replaces what (a) (and any Needles) find with <CLASS>, for text
that is kept, such as a LIMIT line's detail in testvm/test2/stic/limits.json.

Standard library only; Python 3.9 or later; it runs from stdin too
(`ssh HOST python3 -I - strings < leakscan.py`).
"""

import base64
import binascii
import ipaddress
import json
import os
import re
import subprocess
import sys

HOSTS = ("chopin", "cinqcent", "copland", "cage")   # t2lib.KNOWN_HOSTS (this runs on its own too)
# Files that may name the hosts (R6.3): the corpus's index and README.
HOST_NAMES_OK = ("index.tsv", "README.md")
SKIP_DIRS = ("__pycache__",)
SKIP_FILES = (".DS_Store",)
MIN_STRING = 8

_HEX = "0-9A-Fa-f"
_HEXCHARS = frozenset("0123456789abcdefABCDEF")
# Six pairs and one separator, not followed by a hex digit or by more pairs
# with it; what comes before is mac_spans()'s to judge.
_MAC_SEP = re.compile(r"([%s]{2})([:-])(?:[%s]{2}\2){4}[%s]{2}(?![%s]|\2[%s])" % ((_HEX,) * 5))
_MAC_EFI = re.compile(r"MAC[:(]([%s]{12})(?![%s])" % (_HEX, _HEX))
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_IPV6 = re.compile(r"(?<![%s:])(?:[%s]{0,4}:){2,7}[%s]{0,4}(?![%s:])" % ((_HEX,) * 4))
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A bare build signature, user@host:/path (no dotted domain, so not _EMAIL's); host1 is a pseudonym.
_BUILDSIG = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@(?!host\d+:)[A-Za-z0-9-]+:/")
_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|(?:ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) AAAA")
WORD_HOSTS = ("cage",)   # English words inside others (cages, encaged): these count as words only
_HOSTRE = re.compile("|".join(rf"\b{h}\b" if h in WORD_HOSTS else h for h in HOSTS), re.IGNORECASE)
_PSEUDO_MAC = re.compile(r"^bc0{6,}[0-9a-f]{1,4}$")
_NETS4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
_NETS6 = [ipaddress.ip_network(n) for n in ("fe80::/10", "fc00::/7")]
_UUID = re.compile(r"(?<![0-9A-Za-z])[%s]{8}-[%s]{4}-[%s]{4}-[%s]{4}-[%s]{12}(?![0-9A-Za-z])" % ((_HEX,) * 5))

# Keys whose UUID values name types, and GUIDs every machine has: redact.rs
# keeps them (TYPE_KEYS, WELL_KNOWN), so they are no identifiers here.
TYPE_KEYS = ("type", "type_guid", "parttype", "partition_type", "vendor_guid")
WELL_KNOWN = (
    "8be4df61-93ca-11d2-aa0d-00e098032b8c",  # EFI global variables
    "4a67b082-0a4c-41cf-b6c7-440b29bb8c4f",  # systemd's loader interface
    "605dab50-e046-4300-abb6-3dd810dd8b23",  # shim
    "77fa9abd-0359-4d32-bd60-28f4e78f784b",  # Microsoft
    "d719b2cb-3d3a-4596-a3bc-dad00e67656f",  # EFI image security database
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
)
SERIAL_KEYS = ("serial", "wwid", "serial_number", "wwn")
GUID_KEYS = ("guid", "pool_guid", "top_guid", "vdev_guid", "load_guid", "vdev_root_guid")
HEX_KEYS = ("disk_signature", "duid", "hostid", "ufsid", "volume_id")
_LVM_ID = re.compile(r"^[0-9A-Za-z]{6}(?:-[0-9A-Za-z]{4}){5}-[0-9A-Za-z]{6}$")


class ScanError(Exception):
    """What keeps a scan from being complete: its message names the file and
    the line, never what's in them."""


class Finding:
    def __init__(self, path, where, cls, shape):
        self.path, self.where, self.cls, self.shape = path, where, cls, shape

    def __str__(self):
        return "%s: %s: %s (%s)" % (self.path, self.where, self.cls, self.shape)

    def __repr__(self):
        return "Finding(%r)" % str(self)


def is_blob_key(key):
    return key == "b64" or key.endswith("_b64") or key.endswith("_hex") or key == "first_bytes"


def decode_blob(key, value):
    """A blob value's bytes, or None when it isn't one (or doesn't decode)."""
    try:
        if key == "b64" or key.endswith("_b64"):
            return base64.b64decode(value, validate=True)
        if len(value) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in value):
            return binascii.unhexlify(value)
    except (ValueError, binascii.Error):
        return None
    return None


def blob_texts(b):
    """A blob read as text: latin-1, and UTF-16LE from byte 0 and byte 1."""
    out = [("latin-1", b.decode("latin-1"))]
    for off in (0, 1):
        part = b[off:]
        part = part[:len(part) - len(part) % 2]
        out.append(("utf-16le@%d" % off, part.decode("utf-16le", errors="replace")))
    return out


def _mac_digits_ok(d):
    """MAC digits (12 hex) that identify nothing: redact.rs's pseudonyms, zeros,
    broadcast."""
    d = d.lower()
    return (len(d) == 12 and bool(_PSEUDO_MAC.match(d))) or set(d) == {"0"} or d == "ffffffffffff"


def mac_spans(text):
    """[(start, end, separator, digits)] of the xx:xx:... and xx-xx-... MACs in
    TEXT: six hex pairs not glued to a hex digit before them, nor inside a
    longer run of pairs with the same separator (an md UUID's or a time's
    parts are no MAC)."""
    out = []
    for m in _MAC_SEP.finditer(text):
        a, sep = m.start(), m.group(2)
        if a >= 1 and text[a - 1] in _HEXCHARS:
            continue
        if a >= 3 and text[a - 1] == sep and text[a - 2] in _HEXCHARS and text[a - 3] in _HEXCHARS:
            continue
        out.append((a, m.end(), sep, re.sub("[:-]", "", m.group(0)).lower()))
    return out


def pattern_spans(text, allow_hosts=False):
    """[(start, end, class, shape)] of what (a) finds in TEXT."""
    out = []
    for a, b, sep, digits in mac_spans(text):
        if not _mac_digits_ok(digits):
            out.append((a, b, "mac", "6 hex pairs, '%s'-separated" % sep))
    for m in _MAC_EFI.finditer(text):
        if not _mac_digits_ok(m.group(1)):
            out.append((m.start(), m.end(), "mac", "MAC%s and 12 hex" % m.group(0)[3]))
    for m in _IPV4.finditer(text):
        try:
            a = ipaddress.IPv4Address(m.group(0))
        except ValueError:
            continue
        if any(a in n for n in _NETS4):
            out.append((m.start(), m.end(), "ip", "IPv4 %s" % ("link-local" if a.is_link_local else "private")))
    if "::" in text or text.count(":") >= 7:
        for m in _IPV6.finditer(text):
            try:
                a = ipaddress.IPv6Address(m.group(0))
            except ValueError:
                continue
            if any(a in n for n in _NETS6):
                out.append((m.start(), m.end(), "ip", "IPv6 %s" % ("link-local" if a.is_link_local else "unique-local")))
    if "@" in text:
        for m in _EMAIL.finditer(text):
            out.append((m.start(), m.end(), "email", "an e-mail address"))
        for m in _BUILDSIG.finditer(text):
            out.append((m.start(), m.end() - 2, "host", "a build signature's user@host"))
    for m in _KEY.finditer(text):
        out.append((m.start(), m.end(), "key", "a private key" if "PRIVATE" in m.group(0) else "an SSH public key"))
    if not allow_hosts:
        for m in _HOSTRE.finditer(text):
            out.append((m.start(), m.end(), "host", "a test host's name"))
    return out


def pattern_findings(text, allow_hosts=False):
    """[(class, shape)] of what (a) finds in TEXT."""
    return [(c, s) for _, _, c, s in pattern_spans(text, allow_hosts)]


def scrub_text(text, needles=(), allow_hosts=False):
    """TEXT with each thing (a) finds, and each of NEEDLES' texts, replaced
    by <CLASS> (for text that is kept, such as a LIMIT line's detail):
    (the text, how many were replaced)."""
    spans = [(a, b, c) for a, b, c, _ in pattern_spans(text, allow_hosts)]
    low = text.lower()
    for n in needles:
        for t in n.texts:
            i = low.find(t)
            while i >= 0:
                spans.append((i, i + len(t), "host string"))
                i = low.find(t, i + 1)
    merged = []
    for a, b, c in sorted(spans):
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]), merged[-1][2])
        else:
            merged.append((a, b, c))
    out, last = [], 0
    for a, b, c in merged:
        out += [text[last:a], "<%s>" % c]
        last = b
    out.append(text[last:])
    return "".join(out), len(merged)


# ---- strings ----

def _mac_forms(d):
    d = d.lower()
    pairs = [d[i:i + 2] for i in range(0, 12, 2)]
    return [d, ":".join(pairs), "-".join(pairs)]


class Needle:
    """A string to find: its text forms (matched case-insensitively) and its
    byte forms (in blobs)."""

    def __init__(self, kind, texts, raw=()):
        self.kind = kind
        self.texts = sorted({t.lower() for t in texts if len(t.encode()) >= MIN_STRING})
        bs = set()
        for t in self.texts:
            for v in (t, t.upper()):
                bs.add(v.encode("utf-8"))
                bs.add(v.encode("utf-16le"))
        bs.update(r for r in raw if len(r) >= 6)
        self.bytes = sorted(bs)

    def __bool__(self):
        return bool(self.texts or self.bytes)


def needle_for(kind, value):
    """The Needle for one host string."""
    v = value.strip()
    if kind == "mac":
        d = re.sub("[^0-9A-Fa-f]", "", v)
        if len(d) != 12 or _mac_digits_ok(d):
            return None
        return Needle(kind, _mac_forms(d), [binascii.unhexlify(d)])
    if kind == "ip":
        try:
            a = ipaddress.ip_address(v.split("/")[0].split("%")[0])
        except ValueError:
            return None
        if a.is_loopback or a.is_link_local or a.is_unspecified:
            return None
        forms = [str(a)]
        if a.version == 6:
            forms.append(a.exploded)
        return Needle(kind, forms)
    return Needle(kind, [v]) if len(v.encode()) >= MIN_STRING else None


def read_strings(path):
    """The Needles of a strings file.  A line that isn't a {"k": KIND, "v":
    VALUE} object is a ScanError (named by its number only)."""
    out = []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        raise ScanError("--strings %s: %s" % (path, e.strerror or e))
    with f:
        for i, l in enumerate(f, 1):
            l = l.strip()
            if not l:
                continue
            try:
                o = json.loads(l)
            except ValueError:
                o = None
            if not isinstance(o, dict) or not isinstance(o.get("v"), str) or not isinstance(o.get("k", ""), str):
                raise ScanError("--strings %s: line %d isn't a {\"k\", \"v\"} object (a torn or foreign line)" % (path, i))
            n = needle_for(o.get("k") or "string", o["v"])
            if n:
                out.append(n)
    return out


def git_email_needles(repo):
    """The Needle of REPO's git user.email.  No address at all is a ScanError:
    the scan was asked to look for one."""
    try:
        p = subprocess.run(["git", "-C", repo, "config", "user.email"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ScanError("--git-email %s: git didn't run: %s" % (repo, e))
    if not p.stdout.strip():
        raise ScanError("--git-email %s: git config user.email gives no address" % repo)
    n = needle_for("git e-mail", p.stdout)
    return [n] if n else []


def _uuid_bytes(u):
    b = binascii.unhexlify(u.replace("-", ""))
    mixed = b[3::-1] + b[5:3:-1] + b[7:5:-1] + b[8:]
    return [b, mixed]


def raw_needles(raw):
    """The identifiers of a raw dump (parsed JSON), as Needles."""
    types, uuids, serials, hexes, decs, macs = set(), set(), set(), set(), set(), set()

    def walk(v, key, parent):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k, key)
        elif isinstance(v, list):
            for x in v:
                walk(x, key, parent)
        elif isinstance(v, str):
            if is_blob_key(key):
                b = decode_blob(key, v)
                for _, t in blob_texts(b) if b else ():
                    for m in _MAC_EFI.finditer(t):
                        macs.add(m.group(1).lower())
                return
            for _, _, _, digits in mac_spans(v):
                macs.add(digits)
            for m in _MAC_EFI.finditer(v):
                macs.add(m.group(1).lower())
            if key in TYPE_KEYS:
                types.update(u.lower() for u in _UUID.findall(v))
                return
            if key == "id" and _LVM_ID.match(v.strip()):
                hexes.add(v.strip().lower())
                hexes.add(v.strip().replace("-", "").lower())
            if key in HEX_KEYS:
                t = v.strip().lower()
                t = t[2:] if t.startswith("0x") else t
                if len(t) >= MIN_STRING and re.match(r"^[0-9a-f-]+$", t) and set(t) - {"0", "-"}:
                    hexes.add(t)
            if key.endswith("uuid") or key.endswith("guid") or key == "machine_id":
                found = [u.lower() for u in _UUID.findall(v)]
                uuids.update(found)
                t = v.strip()
                if key == "machine_id" and re.match(r"^[0-9a-fA-F]{32}$", t):
                    hexes.add(t.lower())
                elif not found and t:
                    hexes.add(t.lower())
            if key in SERIAL_KEYS and v.strip():
                t = v.strip()
                serials.add(t)
                if "." in t:
                    serials.add(t.split(".", 1)[1])
            if key in GUID_KEYS and v.strip().isdigit():
                decs.add(v.strip())
            if key == "links" or "/by-id/" in v:
                for name in re.findall(r"/by-id/(?:ata|scsi|usb|nvme|virtio|mmc)-([^/\"\s]+)", v):
                    name = re.sub(r"-part\d+$", "", name)
                    serials.add(name.split("_")[-1])
        elif isinstance(v, bool):
            return
        elif isinstance(v, int) and key in GUID_KEYS + ("hostid",) and v >= 10000000:
            decs.add(str(v))

    walk(raw, "", "")
    out = []
    for u in sorted(uuids - types - set(WELL_KNOWN)):
        out.append(Needle("raw dump uuid", [u, u.replace("-", "")], _uuid_bytes(u)))
    seen = set()
    for h in sorted(hexes):
        # An MBR partition's PARTUUID is its disk's signature and a number:
        # the signature is the identifier.  All zeros identify nothing.
        if re.match(r"^[0-9a-f]{8}-[0-9a-f]{2}$", h):
            h = h[:8]
        if len(h) >= MIN_STRING and h not in WELL_KNOWN and h not in seen and set(re.sub("[^0-9a-z]", "", h)) - {"0"}:
            seen.add(h)
            out.append(Needle("raw dump id", [h]))
    for s in sorted(serials):
        out.append(Needle("raw dump serial", [s]))
    for d in sorted(decs):
        out.append(Needle("raw dump GUID", [d]))
    for m in sorted(macs):
        if not _mac_digits_ok(m):
            out.append(Needle("raw dump MAC", _mac_forms(m), [binascii.unhexlify(m)]))
    return [n for n in out if n]


def needle_findings(text, needles):
    low = text.lower()
    return [n.kind for n in needles if any(t in low for t in n.texts)]


def needle_findings_bytes(b, needles):
    out = []
    for n in needles:
        if any(x in b for x in n.bytes):
            out.append(n.kind)
    return out


# ---- scanning files ----

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


def scan_json(v, rel, allow_hosts, needles):
    """Findings in a parsed JSON file.  Paths name a key only when it's a
    plain word that holds no finding; any other key is [#N], its place in its
    object, since a key can hold the very text found (topology keys, EFI
    variable names)."""
    out = []

    def add(where, found, cls_shape):
        for c, s in found:
            out.append(Finding(rel, where, c, s if not cls_shape else cls_shape + ", " + s))

    def walk(x, path, key):
        if isinstance(x, dict):
            for i, (k, y) in enumerate(x.items()):
                kf = pattern_findings(k, allow_hosts)
                kn = needle_findings(k, needles)
                kp = "%s.%s" % (path, k) if _PLAIN_KEY.match(k) and not kf and not kn else "%s[#%d]" % (path, i)
                add(kp + " (a key)", kf, "")
                for kind in kn:
                    out.append(Finding(rel, kp + " (a key)", "host string", kind))
                walk(y, kp, k)
        elif isinstance(x, list):
            for i, y in enumerate(x):
                walk(y, "%s[%d]" % (path, i), key)
        elif isinstance(x, str):
            b = decode_blob(key, x) if is_blob_key(key) else None
            if b is not None:
                # The blob's own text is hex or base64, which holds none of
                # the patterns but a host name by chance: its bytes are what
                # count.
                for how, t in blob_texts(b):
                    add(path, pattern_findings(t, allow_hosts), "blob as " + how)
                for kind in needle_findings_bytes(b, needles):
                    out.append(Finding(rel, path, "host string", kind + ", in the blob's bytes"))
            else:
                add(path, pattern_findings(x, allow_hosts), "")
            for kind in needle_findings(x, needles):
                out.append(Finding(rel, path, "host string", kind))
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            for kind in needle_findings(str(x), needles):
                out.append(Finding(rel, path, "host string", kind))

    walk(v, "$", "")
    return out


def scan_text(text, rel, allow_hosts, needles):
    out = []
    for i, l in enumerate(text.splitlines(), 1):
        for c, s in pattern_findings(l, allow_hosts):
            out.append(Finding(rel, "line %d" % i, c, s))
        for kind in needle_findings(l, needles):
            out.append(Finding(rel, "line %d" % i, "host string", kind))
    return out


def files_under(paths):
    """(path, link) for what to scan under PATHS: every file, and every
    symlink found inside a directory (to a file or a directory, or dangling;
    link True: its target's name is scanned, it is never followed), but
    __pycache__, *.pyc and .DS_Store.  A PATH named itself is followed."""
    for p in paths:
        if os.path.isdir(p):
            for d, dirs, names in os.walk(p):
                links = [x for x in dirs if os.path.islink(os.path.join(d, x))]
                dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and x not in links)
                for n in sorted(names + links):
                    if n in SKIP_FILES or n.endswith(".pyc"):
                        continue
                    f = os.path.join(d, n)
                    yield f, os.path.islink(f)
        else:
            yield p, False


def scan_file(path, rel=None, allow_hosts=False, needles=(), raw_dir=None, errors=None, stats=None, link=False):
    """The findings in one file (with LINK, in the name a symlink points at).
    What keeps the scan from being complete goes to ERRORS (a list), or
    without one is raised as ScanError: a file that can't be read, and with
    RAW_DIR a GUEST/src.json without a raw twin (RAW_DIR/GUEST/src.json)
    that parses, which is still scanned for the rest.  STATS (a dict) counts
    "src_json" and "raw_twins"."""
    rel = rel or path
    allow = allow_hosts or os.path.basename(path) in HOST_NAMES_OK
    needles = list(needles)
    problems = [] if errors is None else errors
    stats = {} if stats is None else stats
    if link:
        try:
            target = os.readlink(path)
        except OSError as e:
            problems.append("%s: a symlink that can't be read: %s" % (rel, e.strerror or e))
            target = ""
        out = scan_text(target, rel + " (a symlink)", allow, needles)
        if errors is None and problems:
            raise ScanError(problems[0])
        return out
    try:
        if not os.path.isfile(path):
            raise OSError(0, "not a file, a directory or a symlink")
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        problems.append("%s: can't be read: %s" % (rel, e.strerror or e))
        if errors is None:
            raise ScanError(problems[0])
        return []
    if raw_dir and os.path.basename(path) == "src.json":
        stats["src_json"] = stats.get("src_json", 0) + 1
        rp = os.path.join(raw_dir, os.path.basename(os.path.dirname(os.path.abspath(path))), "src.json")
        try:
            with open(rp, encoding="utf-8", errors="replace") as f:
                needles += raw_needles(json.load(f))
            stats["raw_twins"] = stats.get("raw_twins", 0) + 1
        except OSError as e:
            problems.append("%s: no raw twin %s (%s): its raw identifiers can't be searched" % (rel, rp, e.strerror or e))
        except ValueError:
            problems.append("%s: its raw twin %s isn't JSON: its raw identifiers can't be searched" % (rel, rp))
    text = data.decode("utf-8", errors="replace")
    out = None
    if path.endswith(".json"):
        try:
            v = json.loads(text)
        except ValueError:
            v = None
        if v is not None:
            out = scan_json(v, rel, allow, needles)
    elif path.endswith(".jsonl"):
        out = []
        for i, l in enumerate(text.splitlines(), 1):
            try:
                v = json.loads(l)
            except ValueError:
                out += scan_text(l, rel, allow, needles)
                continue
            out += scan_json(v, "%s line %d" % (rel, i), allow, needles)
    if out is None:
        out = scan_text(text, rel, allow, needles)
        if b"\0" in data:
            for how, t in blob_texts(data)[1:]:
                for c, s in pattern_findings(t, allow):
                    out.append(Finding(rel, "the file as " + how, c, s))
            for kind in needle_findings_bytes(data, needles):
                out.append(Finding(rel, "the file's bytes", "host string", kind))
    if errors is None and problems:
        raise ScanError(problems[0])
    return out


def scan_paths(paths, needles=(), raw_dir=None, allow_hosts=False, errors=None, stats=None):
    """Every finding under PATHS, in file order.  ERRORS and STATS as
    scan_file's; without ERRORS the first problem is raised (ScanError)."""
    out = []
    problems = [] if errors is None else errors
    for f, link in files_under(paths):
        out += scan_file(f, f, allow_hosts, needles, raw_dir, problems, stats, link)
    if errors is None and problems:
        raise ScanError(problems[0])
    return out


# ---- this host's strings ----

def host_strings(vms):
    """[(kind, value)] of this host."""
    out = []
    vms = os.path.expanduser(vms)
    try:
        guests = sorted(os.listdir(vms))
    except OSError:
        guests = []
    for g in guests:
        for n in ("console-passphrase", "luks-passphrase", "zfs-passphrase", "root-password"):
            p = os.path.join(vms, g, n)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    v = f.read().strip("\n")
            except OSError:
                continue
            if v:
                out.append(("passphrase", v))

    def readf(p):
        try:
            with open(p, errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""
    for d in sorted(os.listdir("/sys/block")) if os.path.isdir("/sys/block") else []:
        for n in ("device/serial", "device/wwid", "wwid"):
            v = readf(os.path.join("/sys/block", d, n))
            if v:
                out.append(("serial", v))
    for d in sorted(os.listdir("/sys/class/nvme")) if os.path.isdir("/sys/class/nvme") else []:
        v = readf(os.path.join("/sys/class/nvme", d, "serial"))
        if v:
            out.append(("serial", v))
    for n in sorted(os.listdir("/dev/disk/by-id")) if os.path.isdir("/dev/disk/by-id") else []:
        n = re.sub(r"-part\d+$", "", n)
        if n.startswith("wwn-0x"):
            out.append(("serial", n[6:]))
        elif "_" in n:
            out.append(("serial", n.rsplit("_", 1)[1]))
    for fam in ("-4", "-6"):
        try:
            p = subprocess.run(["ip", "-o", fam, "addr"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for m in re.finditer(r"\binet6? ([0-9A-Fa-f:.]+)/", p.stdout):
            out.append(("ip", m.group(1)))
    try:
        p = subprocess.run(["ip", "-o", "link"], capture_output=True, text=True, timeout=10)
        for m in re.finditer(r"link/\S+ ([0-9a-f]{2}(?::[0-9a-f]{2}){5})", p.stdout):
            out.append(("mac", m.group(1)))
    except (OSError, subprocess.TimeoutExpired):
        pass
    for args in (["git", "config", "--global", "user.email"], ["git", "-C", os.path.expanduser("~/backotter-work"), "config", "user.email"]):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.stdout.strip():
            out.append(("git e-mail", p.stdout.strip()))
    seen, uniq = set(), []
    for k, v in out:
        if (k, v) not in seen:
            seen.add((k, v))
            uniq.append((k, v))
    return uniq


USAGE = """usage: leakscan.py scan [--allow-hosts] [--strings FILE] [--git-email REPO] [--raw DIR] PATH...
       leakscan.py strings [--vms DIR]"""


def main(argv):
    if not argv or argv[0] not in ("scan", "strings"):
        print(USAGE, file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    opts = {"--strings": [], "--git-email": [], "--raw": [], "--vms": []}
    flags, paths = set(), []
    i = 0
    while i < len(args):
        a = args[i]
        if a in opts:
            if i + 1 >= len(args):
                print(USAGE, file=sys.stderr)
                return 2
            opts[a].append(args[i + 1])
            i += 2
        elif a == "--allow-hosts":
            flags.add(a)
            i += 1
        elif a.startswith("--"):
            print(USAGE, file=sys.stderr)
            return 2
        else:
            paths.append(a)
            i += 1
    if cmd == "strings":
        if paths or opts["--strings"] or opts["--git-email"] or opts["--raw"] or flags:
            print(USAGE, file=sys.stderr)
            return 2
        for k, v in host_strings(opts["--vms"][-1] if opts["--vms"] else "~/backotter-work/vms"):
            print(json.dumps({"k": k, "v": v}))
        return 0
    if not paths or opts["--vms"] or len(opts["--raw"]) > 1:
        print(USAGE, file=sys.stderr)
        return 2
    for p in paths:
        if not os.path.exists(p):
            print("leakscan.py: %s: no such file or directory" % p, file=sys.stderr)
            return 2
    raw = opts["--raw"][0] if opts["--raw"] else None
    if raw is not None and not os.path.isdir(raw):
        print("leakscan.py: --raw %s: not a directory" % raw, file=sys.stderr)
        return 2
    needles = []
    try:
        for f in opts["--strings"]:
            needles += read_strings(f)
        for r in opts["--git-email"]:
            needles += git_email_needles(r)
    except ScanError as e:
        print("leakscan.py: %s" % e, file=sys.stderr)
        return 2
    errors, stats = [], {}
    found = scan_paths(paths, needles, raw, "--allow-hosts" in flags, errors, stats)
    for f in found:
        print(f)
    for e in errors:
        print("leakscan.py: error: %s" % e, file=sys.stderr)
    n = len(list(files_under(paths)))
    twins = ""
    if raw is not None:
        twins = ", raw twins for %d of %d src.json" % (stats.get("raw_twins", 0), stats.get("src_json", 0))
    print("leakscan.py: %d finding(s) in %d file(s)%s%s%s" % (len(found), n, ", %d host string(s)" % len(needles) if needles else "", twins,
                                                            "; %d error(s): the scan is incomplete" % len(errors) if errors else ""), file=sys.stderr)
    if errors:
        return 2
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
