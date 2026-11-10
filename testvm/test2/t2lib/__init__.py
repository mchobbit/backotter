"""t2lib: Test2's library, imported by testvm/test2/t2 (on the Mac) and
testvm/test2/t2-host (on the KVM test hosts), each from its own directory.
Stdlib only; Python 3.9 or newer.

The ledger core (package P1 of the Test2 build, docs/design/test2.md "Shared
pieces to build"): this module holds what every other module shares, the
roots, canonical JSON and times.

- ledger.py   rows, the writer and reader, pull and merge, the wear fill
- keys.py     inputs, keys and group digests; GitSource, TreeSource, MapSource
- facts.py    the host probe, run from the Mac (facts_host.py is the probe)
- copies.py   private copies of testvm/, stick/ and holt/; the committed rule
- wear.py     jobs.log tag sums, diskstats windows, SMART day deltas
- hostrun.py  ssh, detached starts, the host context row builders get

Roots (every one can be overridden, and tests never use the defaults):
  MAC_ROOT   ~/backotter-work/test2 on the Mac        T2_MAC_ROOT
  HOST_ROOT  /bkoscratch/test2 on each host            T2_HOST_ROOT
  COPY_ROOT  ~/backotter-work/t2/copy on each host     T2_COPY_ROOT
  BKO_GIT    ~/git on the Mac                          BKO_GIT
  the host ledger file, HOST_ROOT/ledger/HOST.jsonl   T2_LEDGER
  the work directory, ~/backotter-work                T2_WORK (t2-host --work)
As root (the HoltTest drivers run t2-host under sudo -E) "~" is SUDO_USER's
home from the password database, never $HOME.
"""

import hashlib
import json
import os
import re
import socket
import time

V = 1   # the row schema's version

# Key groups (contract 1.3): the hard ones make the key; plumbing and soft are
# digested beside it but never keyed.
HARD = ("bin", "harness", "judge", "guest", "xguest", "rescuer", "host")
GROUPS = HARD + ("plumbing", "soft")
FAMILIES = ("gate", "stic", "holt", "guest", "img")   # guest and img: reserved for later pieces
KINDS = ("run", "carried", "class")
VERDICTS = ("PASS", "FAIL", "FLAKY-FAIL", "HARNESS", "LOST")
CLASSES = ("pass", "fail", "harness", "lost", "known-flake")
NEED_VALUES = ("PASS", "FAIL", "LIMIT", "LIMIT-NEW", "SKIP", "DIFF")

# What a private copy (and every source a key reads) leaves out (contract 1.3).
SCOPE = ("testvm", "stick", "holt")
EXCLUDE_NAMES = ("__pycache__", ".DS_Store")
EXCLUDE_PREFIXES = ("holt/target/",)

DEFAULT_MAC_ROOT = "~/backotter-work/test2"
DEFAULT_HOST_ROOT = "/bkoscratch/test2"
DEFAULT_COPY_ROOT = "~/backotter-work/t2/copy"
DEFAULT_BKO_GIT = "~/git"

UTC_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")


# --- canonical JSON and digests ---------------------------------------------------

def canonical(o):
    """The canonical JSON text of O (contract 1.3): sorted keys, no spaces, ASCII."""
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data):
    """sha256 hex of bytes, or of a str's UTF-8."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_sha256(path, follow=True):
    """sha256 hex of a file's bytes; a symlink (with follow=False) hashes its target text.
    None when it isn't there."""
    try:
        if not follow and os.path.islink(path):
            return sha256_hex(os.readlink(path))
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, NotADirectoryError):
        return None


# --- times -------------------------------------------------------------------------

def utc_now(t=None):
    """UTC ISO time, to the second: 2026-11-08T03:41:07Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def utc_stamp(t=None):
    """The compact form ids use: 20261108T034107Z."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if t is None else t))


def parse_utc(s):
    """Epoch seconds of a UTC ISO time (2026-11-08T03:41:07Z, or with fractional seconds), or None."""
    if not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?Z\Z", s)
    if not m:
        return None
    import calendar
    y, mo, d, h, mi, se = (int(x) for x in m.groups()[:6])
    return calendar.timegm((y, mo, d, h, mi, se, 0, 0, 0)) + (float(m.group(7)) if m.group(7) else 0.0)


# --- hosts, users and roots ---------------------------------------------------------

def short_host():
    """hostname -s."""
    return socket.gethostname().split(".")[0]


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def work_user_home():
    """The home of the user whose work this is: SUDO_USER's from the password
    database when running as root (never $HOME, which sudo -E keeps or not),
    else this user's."""
    if is_root():
        user = os.environ.get("SUDO_USER")
        if user and user != "root":
            try:
                import pwd
                return pwd.getpwnam(user).pw_dir
            except (KeyError, ImportError):
                pass
        try:
            import pwd
            return pwd.getpwuid(os.geteuid()).pw_dir
        except (KeyError, ImportError):
            return "/root"
    return os.path.expanduser("~")


def expand(path):
    """~ and $VARS in PATH, ~ being work_user_home()."""
    if path is None:
        return None
    path = os.path.expandvars(path)
    if path == "~" or path.startswith("~/"):
        path = work_user_home() + path[1:]
    return path


def work_dir(override=None):
    """~/backotter-work (T2_WORK, or t2-host --work, overrides it)."""
    return expand(override or os.environ.get("T2_WORK") or "~/backotter-work")


def mac_root():
    return expand(os.environ.get("T2_MAC_ROOT") or DEFAULT_MAC_ROOT)


def host_root():
    return expand(os.environ.get("T2_HOST_ROOT") or DEFAULT_HOST_ROOT)


def copy_root():
    return expand(os.environ.get("T2_COPY_ROOT") or DEFAULT_COPY_ROOT)


def bko_git():
    return expand(os.environ.get("BKO_GIT") or DEFAULT_BKO_GIT)


def is_default(name):
    """True when root NAME (mac, host, copy) is its default: tests refuse that."""
    env, default = {"mac": ("T2_MAC_ROOT", DEFAULT_MAC_ROOT), "host": ("T2_HOST_ROOT", DEFAULT_HOST_ROOT),
                    "copy": ("T2_COPY_ROOT", DEFAULT_COPY_ROOT)}[name]
    v = os.environ.get(env)
    return not v or expand(v).rstrip("/") == expand(default).rstrip("/")


def host_ledger_file(host=None):
    """This host's ledger: T2_LEDGER, else HOST_ROOT/ledger/HOST.jsonl."""
    return expand(os.environ.get("T2_LEDGER")) or os.path.join(host_root(), "ledger", (host or short_host()) + ".jsonl")


def mac_ledger_file():
    return os.path.join(mac_root(), "ledger", "mac.jsonl")


# --- paths --------------------------------------------------------------------------

def excluded(relpath):
    """True for what copies and sources leave out: __pycache__/, *.pyc, .DS_Store, holt/target/."""
    parts = relpath.split("/")
    if any(p in EXCLUDE_NAMES for p in parts) or relpath.endswith(".pyc"):
        return True
    return any((relpath + "/").startswith(p) for p in EXCLUDE_PREFIXES)


def tree_files(root, scope=None):
    """{relpath: abs path} of ROOT's regular files and symlinks (relpaths with /),
    the exclusions left out; SCOPE limits it to those top-level names."""
    out = {}
    tops = [s for s in scope if os.path.lexists(os.path.join(root, s))] if scope else [None]
    for top in tops:
        base = os.path.join(root, top) if top else root
        if top and (os.path.islink(base) or not os.path.isdir(base)):
            if not excluded(top):
                out[top] = base
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir + "/"
            if not top and rel_dir == "":
                dirnames[:] = [d for d in dirnames if d != ".git"]
            keep = []
            for d in sorted(dirnames):
                p = os.path.join(dirpath, d)
                if excluded(rel_dir + d):
                    continue
                if os.path.islink(p):   # a link to a directory: listed as a link, not walked
                    out[rel_dir + d] = p
                    continue
                keep.append(d)
            dirnames[:] = keep
            for f in filenames:
                rel = rel_dir + f
                if not excluded(rel):
                    out[rel] = os.path.join(dirpath, f)
    return dict(sorted(out.items()))
