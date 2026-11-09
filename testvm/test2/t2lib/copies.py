#!/usr/bin/env python3
"""Private copies of testvm/, stick/ and holt/ on the hosts (contract 1.5; ruling R12).

Every run copies its checkout to each host it uses before it starts anything
(the pattern of sync-hosts-shared-dirs.md): any session's sync changes the
shared ~/backotter-work tree under a run.

    preview(checkout)      commit, dirty map, ID and the tree's hashes, without copying
    make(checkout, hosts)  the copy COPY_ROOT/ID on each host: made, or reused once verified
    verify(host, path, hashes)
    gc(hosts)              delete complete copies over 7 days old that no recent run names
    committed_ok(row, head_hashes)   the committed rule
    head_hashes(checkout)  HEAD's content sha256 per path, for committed_ok

ID is HEAD's first 7 hex when testvm/, stick/ and holt/ are clean, else
SHORT.dHASH8, HASH8 the first 8 hex of the sha256 of the canonical JSON of
the dirty map ({path: working-tree content sha256, or null for a deletion},
from git status --porcelain=v1 -z --untracked-files=all, the exclusions left
out).  On a host, under flock COPY_ROOT/.ID.lock (120 s; taking a complete copy
or finishing one touches COPY_ROOT/.ID.used, and gc keeps a copy taken in the
last 7 days): a complete copy
(ID/.t2-complete) is verified and reused (one that differs is set aside as
.ID.bad.TIME, for gc); else the checkout is rsynced into
ID.part.PID (--link-dest at the newest complete copy: unchanged files become
hard links), hashed against the Mac's, given COMMIT (SHORT or SHORT.dirty,
which ht1-lib.sh's ht_manifest reads) and T2-COPY.json ({id, commit, dirty,
made, from}), renamed to ID (os.rename: never mv into a directory) and marked
complete.  A copy's files never change; a run may add top-level directories
of its own (fs-roundtrip/).

The host side of this file is self-contained (stdlib only): it runs as a
stdin script, "hold ROOT ID" (the lock holder, talking JSON lines),
"hash ROOT [PATH...]" and "gc ROOT HOST_ROOT DAYS [--dry-run]".
"""

import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

SCOPE = ("testvm", "stick", "holt")
EXCLUDE_NAMES = ("__pycache__", ".DS_Store")
EXCLUDE_PREFIXES = ("holt/target/",)
DEFAULT_ROOT = "~/backotter-work/t2/copy"
LOCK_WAIT = 120
ID_RE = re.compile(r"[0-9a-f]{7}(\.d[0-9a-f]{8})?\Z")   # copy_id()'s form
BAD_RE = re.compile(r"\.([0-9a-f]{7}(?:\.d[0-9a-f]{8})?)\.bad\.(\d+)\Z")


# --- shared by both sides --------------------------------------------------------------

def _excluded(rel):
    parts = rel.split("/")
    if any(p in EXCLUDE_NAMES for p in parts) or rel.endswith(".pyc"):
        return True
    return any((rel + "/").startswith(p) for p in EXCLUDE_PREFIXES)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root, paths=SCOPE):
    """{relpath: sha256 hex, or "->target" for a symlink} of ROOT's files under
    PATHS (top-level names or relpaths), the exclusions left out."""
    out = {}
    for top in paths:
        base = os.path.join(root, top)
        if os.path.islink(base) or os.path.isfile(base):
            if not _excluded(top):
                out[top] = "->" + os.readlink(base) if os.path.islink(base) else _sha(base)
            continue
        for dp, dns, fns in os.walk(base):
            rel = os.path.relpath(dp, root).replace(os.sep, "/")
            keep = []
            for d in sorted(dns):
                r = f"{rel}/{d}"
                if _excluded(r):
                    continue
                if os.path.islink(os.path.join(dp, d)):
                    out[r] = "->" + os.readlink(os.path.join(dp, d))
                else:
                    keep.append(d)
            dns[:] = keep
            for f in fns:
                r = f"{rel}/{f}"
                if _excluded(r):
                    continue
                p = os.path.join(dp, f)
                out[r] = "->" + os.readlink(p) if os.path.islink(p) else _sha(p)
    return dict(sorted(out.items()))


def hashes_digest(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def content_sha(value):
    """A hashes value as a content sha256: a symlink's is its target text's (git's view)."""
    if value is None:
        return None
    return hashlib.sha256(value[2:].encode("utf-8", "surrogateescape")).hexdigest() if value.startswith("->") else value


# --- host side ---------------------------------------------------------------------------

def _expand(p):
    p = os.path.expandvars(p)
    return os.path.expanduser(p) if p.startswith("~") else p


def _say(obj):
    sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")
    sys.stdout.flush()


def _rm_tree(p):
    """rm -rf P (one of ours); root-owned leftovers (a root run's) through sudo -n."""
    try:
        shutil.rmtree(p)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        r = subprocess.run(["sudo", "-n", "rm", "-rf", "--one-file-system", p], capture_output=True)
        return r.returncode == 0 and not os.path.lexists(p)


def _complete(root):
    """The complete copies under ROOT, newest first."""
    out = []
    for n in os.listdir(root) if os.path.isdir(root) else []:
        m = os.path.join(root, n, ".t2-complete")
        if not n.startswith(".") and ".part." not in n and os.path.isfile(m):
            out.append((os.stat(m).st_mtime, n))
    return [n for _, n in sorted(out, reverse=True)]


def _lock(path, wait):
    """flock LOCK_EX on PATH (made if missing) within WAIT s: the fd, or None.  The
    lock counts only while PATH is still the file locked (gc removes a deleted
    copy's lock file): else it starts again on the new file."""
    end = time.monotonic() + wait
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                a, b = os.fstat(fd), os.stat(path)
                same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
            except FileNotFoundError:
                same = False
            if same:
                return fd
            os.close(fd)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            continue
        except OSError:
            if time.monotonic() >= end:
                os.close(fd)
                return None
            time.sleep(0.1)


def _touch_used(root, cid):
    """COPY_ROOT/.ID.used: when a run last took this copy (gc keeps it from then)."""
    p = os.path.join(root, f".{cid}.used")
    try:
        with open(p, "a"):
            pass
        os.utime(p, None)
    except OSError:
        pass


def host_hold(root, cid, wait=LOCK_WAIT):
    """The lock holder: takes COPY_ROOT/.ID.lock, says what is there, then does
    what the Mac asks (hash, finish, abort) until its stdin ends.  A part it
    handed out and wasn't asked to finish is removed at the end."""
    root = _expand(root)
    os.makedirs(root, mode=0o755, exist_ok=True)
    if not ID_RE.match(cid or ""):
        _say({"op": "error", "error": f"bad copy id {cid!r}"})
        return 2
    fd = _lock(os.path.join(root, f".{cid}.lock"), wait)
    if fd is None:
        _say({"op": "error", "error": f"no lock on {root}/.{cid}.lock in {wait} s"})
        return 1
    target = os.path.join(root, cid)
    if os.path.isfile(os.path.join(target, ".t2-complete")):
        _touch_used(root, cid)   # a run is taking it (or will set it aside)
    part = f"{cid}.part.{os.getpid()}"
    newest = [n for n in _complete(root) if n != cid]
    _say({"op": "locked", "pid": os.getpid(), "root": root, "path": target, "part": part,
          "complete": os.path.isfile(os.path.join(target, ".t2-complete")), "newest": newest[0] if newest else None})
    finished = False
    for line in sys.stdin.buffer:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        op = req.get("op")
        if op == "hash":
            where = os.path.join(root, req.get("dir") or cid)
            if not os.path.isdir(where):
                _say({"op": "hashes", "error": f"no {where}"})
                continue
            h = tree_hashes(where, req.get("paths") or SCOPE)
            _say({"op": "hashes", "files": h, "digest": hashes_digest(h)})
        elif op == "finish":
            pdir = os.path.join(root, part)
            info = req.get("info") or {}
            try:
                short = (info.get("commit") or "")[:7] + ("" if info.get("dirty") == {} else ".dirty")
                with open(os.path.join(pdir, "COMMIT"), "w", encoding="utf-8") as f:
                    f.write(short + "\n")
                with open(os.path.join(pdir, "T2-COPY.json"), "w", encoding="utf-8") as f:
                    json.dump(info, f, sort_keys=True, indent=1)
                    f.write("\n")
                if os.path.lexists(target) and not os.path.isfile(os.path.join(target, ".t2-complete")):
                    _rm_tree(target)   # a copy that died before it was marked complete
                os.rename(pdir, target)
                open(os.path.join(target, ".t2-complete"), "w").close()
                _touch_used(root, cid)
                finished = True
                _say({"op": "finished", "ok": True, "path": target})
            except OSError as e:
                _say({"op": "finished", "ok": False, "error": f"{type(e).__name__}: {e}"})
        elif op == "abort":
            _rm_tree(os.path.join(root, part))
            _say({"op": "aborted"})
        elif op == "set-aside":
            # A complete copy that no longer matches its checkout: renamed to
            # .ID.bad.TIME (gc deletes it after DAYS), so a fresh one can be made.
            bad = os.path.join(root, f".{cid}.bad.{int(time.time())}")
            try:
                if os.path.isfile(os.path.join(target, ".t2-complete")):
                    os.rename(target, bad)
                _say({"op": "set-aside", "ok": True, "path": bad})
            except OSError as e:
                _say({"op": "set-aside", "ok": False, "error": f"{type(e).__name__}: {e}"})
        elif op == "bye":
            break
    if not finished and os.path.isdir(os.path.join(root, part)):
        _rm_tree(os.path.join(root, part))
    return 0


def host_gc(root, host_root, days, dry=False):
    """Delete the complete copies made and last taken (.ID.used) more than DAYS ago
    that no run directory of the last DAYS days names (HOST_ROOT/runs/*/cells*.json
    "copy": {"id"}), with their .ID.lock and .ID.used, and the copies set aside
    (.ID.bad.TIME) more than DAYS ago."""
    root, host_root = _expand(root), _expand(host_root)
    now, horizon = time.time(), days * 86400
    refs = set()
    for cj in glob.glob(os.path.join(host_root, "runs", "*", "cells*.json")):
        try:
            if now - os.stat(cj).st_mtime > horizon:
                continue
            with open(cj, encoding="utf-8") as f:
                c = json.load(f)
            cid = (c.get("copy") or {}).get("id")
            if cid:
                refs.add(cid)
        except (OSError, ValueError, AttributeError):
            continue
    out = {"deleted": [], "kept": [], "referenced": sorted(refs), "failed": [], "stale_parts": []}
    out["set_aside"] = []
    for n in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        p = os.path.join(root, n)
        bm = BAD_RE.match(n)
        if bm and os.path.isdir(p) and not os.path.islink(p):
            if now - int(bm.group(2)) > horizon:
                if dry or _rm_tree(p):
                    out["set_aside"].append(n)
                else:
                    out["failed"].append(n)
            continue
        if n.startswith(".") or not os.path.isdir(p):
            continue
        if ".part." in n:
            out["stale_parts"].append(n)
            continue
        mark = os.path.join(p, ".t2-complete")
        if not os.path.isfile(mark):
            continue
        made = None
        try:
            import calendar
            with open(os.path.join(p, "T2-COPY.json"), encoding="utf-8") as f:
                m = json.load(f).get("made")
            made = calendar.timegm(time.strptime(m, "%Y-%m-%dT%H:%M:%SZ")) if m else None
        except (OSError, ValueError, TypeError, AttributeError):
            made = None
        made = made or os.stat(mark).st_mtime
        try:
            made = max(made, os.stat(os.path.join(root, f".{n}.used")).st_mtime)
        except OSError:
            pass
        if now - made <= horizon or n in refs:
            out["kept"].append(n)
            continue
        if dry:
            out["deleted"].append(n)
            continue
        fd = _lock(os.path.join(root, f".{n}.lock"), 0)
        if fd is None:
            out["kept"].append(n)   # in use: a maker or another gc holds it
            continue
        try:
            if _rm_tree(p):
                out["deleted"].append(n)
                for x in (f".{n}.used", f".{n}.lock"):   # the lock last, while we hold it
                    try:
                        os.unlink(os.path.join(root, x))
                    except OSError:
                        pass
            else:
                out["failed"].append(n)
        finally:
            os.close(fd)
    return out


def host_main(argv):
    if not argv:
        sys.stderr.write("usage: copies.py hold ROOT ID | hash ROOT [PATH...] | gc ROOT HOST_ROOT DAYS [--dry-run]\n")
        return 2
    if argv[0] == "hold" and len(argv) == 3:
        if not ID_RE.match(argv[2]):
            sys.stderr.write(f"copies.py: bad copy id {argv[2]!r}\n")
            return 2
        return host_hold(argv[1], argv[2])
    if argv[0] == "hash" and len(argv) >= 2:
        h = tree_hashes(_expand(argv[1]), argv[2:] or SCOPE)
        _say({"files": h, "digest": hashes_digest(h)})
        return 0
    if argv[0] == "gc" and len(argv) >= 4:
        _say(host_gc(argv[1], argv[2], float(argv[3]), "--dry-run" in argv[4:]))
        return 0
    sys.stderr.write(f"copies.py: bad arguments {argv!r}\n")
    return 2


# --- the Mac side -------------------------------------------------------------------------

class CopyError(Exception):
    pass


class Preview:
    def __init__(self, checkout, commit, dirty, cid, hashes):
        self.checkout, self.commit, self.dirty, self.id, self.hashes = checkout, commit, dirty, cid, hashes

    @property
    def short(self):
        return self.commit[:7]

    @property
    def clean(self):
        return not self.dirty

    def info(self, made=None):
        from . import utc_now, short_host
        return {"id": self.id, "commit": self.commit, "dirty": self.dirty, "made": made or utc_now(),
                "from": f"{short_host()}:{self.checkout}"}

    def __repr__(self):
        return f"Preview({self.id}, {len(self.dirty)} dirty, {len(self.hashes)} files)"


def _git(checkout, *args):
    # --no-optional-locks: status doesn't rewrite the index (other sessions' git runs beside us)
    p = subprocess.run(["git", "--no-optional-locks", "-C", checkout] + list(args), capture_output=True)
    if p.returncode != 0:
        raise CopyError(f"git {' '.join(args)}: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout


def status_paths(checkout):
    """The paths under testvm/, stick/ and holt/ that git status lists (renames:
    both paths), the exclusions left out."""
    out = _git(checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *SCOPE)
    toks, paths, i = out.split(b"\0"), set(), 0
    while i < len(toks):
        t = toks[i]
        i += 1
        if len(t) < 4:
            continue
        xy, p = t[:2], t[3:].decode("utf-8", "surrogateescape")
        paths.add(p)
        if xy[:1] in (b"R", b"C") and i < len(toks):
            paths.add(toks[i].decode("utf-8", "surrogateescape"))
            i += 1
    return sorted(p for p in paths if not _excluded(p) and p.split("/", 1)[0] in SCOPE)


def copy_id(commit, dirty):
    from . import canonical, sha256_hex
    return commit[:7] if not dirty else commit[:7] + ".d" + sha256_hex(canonical(dirty))[:8]


def preview(checkout):
    """Preview(checkout, commit, dirty, id, hashes) of the checkout now.  The
    status is read before and after the hashes, and again until they agree."""
    checkout = os.path.abspath(checkout)
    commit = _git(checkout, "rev-parse", "HEAD").decode().strip()
    for _ in range(5):
        before = status_paths(checkout)
        hashes = tree_hashes(checkout, SCOPE)
        after = status_paths(checkout)
        if before == after:
            break
    else:
        raise CopyError("the checkout keeps changing; try again")
    dirty = {}
    for p in after:
        full = os.path.join(checkout, p)
        if p in hashes:
            dirty[p] = content_sha(hashes[p])
        elif os.path.isdir(full) and not os.path.islink(full):
            continue   # a directory git lists (an empty one, or a submodule)
        else:
            dirty[p] = None
    return Preview(checkout, commit, dirty, copy_id(commit, dirty), hashes)


def _self_source():
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        return f.read()


def _diff(a, b, n=8):
    keys = sorted(set(a) | set(b))
    d = [k for k in keys if a.get(k) != b.get(k)]
    return d[:n], len(d)


class CopyResult:
    def __init__(self, host, path, cid, made, hashes_digest_):
        self.host, self.path, self.id, self.made, self.digest = host, path, cid, made, hashes_digest_

    def __repr__(self):
        return f"CopyResult({self.host}:{self.path}, {'made' if self.made else 'reused'})"


def _make_one(host, pv, root, timeout, log):
    from . import hostrun
    s = hostrun.session(host, _self_source(), ["hold", root, pv.id], timeout=timeout)
    try:
        st = s.recv(timeout=LOCK_WAIT + 60)
        if not st or st.get("op") != "locked":
            rc, err = s.close()
            raise CopyError(f"{host}: no copy lock: {(st or {}).get('error') or err.strip()[-300:] or rc}")
        if st["complete"]:
            h = s.ask({"op": "hash"}, timeout=timeout)
            if h.get("files") == pv.hashes:
                log(f"copy {pv.id} on {host}: reused, {len(pv.hashes)} files verified")
                return CopyResult(host, st["path"], pv.id, False, hashes_digest(pv.hashes))
            some, n = _diff(pv.hashes, h.get("files") or {})
            aside = s.ask({"op": "set-aside"}, timeout=timeout)
            if not aside or not aside.get("ok"):
                raise CopyError(f"{host}: copy {st['path']} differs from the checkout in {n} file(s) "
                                f"({', '.join(some)}) and couldn't be set aside: {(aside or {}).get('error')}")
            log(f"copy {pv.id} on {host}: differed from the checkout in {n} file(s) ({', '.join(some)}); "
                f"set aside as {aside['path']}, making a fresh one")
        part_dir = os.path.join(st["root"], st["part"])
        link = os.path.join(st["root"], st["newest"]) if st.get("newest") else None
        srcs = [os.path.join(pv.checkout, d) for d in SCOPE if os.path.isdir(os.path.join(pv.checkout, d))]
        r = hostrun.rsync(srcs, host, part_dir, link_dest=link, timeout=timeout)
        if r.rc != 0:
            s.ask({"op": "abort"})
            raise CopyError(f"{host}: rsync failed ({r.rc}): {r.errtext.strip()[-300:]}")
        h = s.ask({"op": "hash", "dir": st["part"]}, timeout=timeout)
        if h.get("files") != pv.hashes:
            some, n = _diff(pv.hashes, h.get("files") or {})
            s.ask({"op": "abort"})
            raise CopyError(f"{host}: the copy differs from the checkout in {n} file(s) (did it change?): "
                            f"{', '.join(some)}")
        fin = s.ask({"op": "finish", "info": pv.info()}, timeout=timeout)
        if not fin or not fin.get("ok"):
            raise CopyError(f"{host}: couldn't finish the copy: {(fin or {}).get('error')}")
        log(f"copy {pv.id} on {host}: made at {fin['path']}" + (f" (links to {st['newest']})" if link else ""))
        return CopyResult(host, fin["path"], pv.id, True, hashes_digest(pv.hashes))
    finally:
        try:
            s.send({"op": "bye"})
        except OSError:
            pass
        s.close()


def make(checkout, hosts, root=None, timeout=900, log=None):
    """The private copy of CHECKOUT on each of HOSTS (in parallel): {host: CopyResult}.
    CopyError when a host fails or the checkout changed meanwhile."""
    import threading
    from . import hostrun
    log = log or (lambda m: None)
    pv = preview(checkout)
    rr = hostrun.host_path(root or os.environ.get("T2_COPY_ROOT") or DEFAULT_ROOT)
    out, errs = {}, {}

    def one(h):
        try:
            out[h] = _make_one(h, pv, rr, timeout, log)
        except Exception as e:
            errs[h] = e

    ths = [threading.Thread(target=one, args=(h,)) for h in hosts]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if errs:
        raise CopyError("; ".join(f"{h}: {e}" for h, e in sorted(errs.items())))
    again = preview(checkout)
    if again.id != pv.id or again.hashes != pv.hashes:
        raise CopyError(f"the checkout changed while it was copied ({pv.id} -> {again.id}); run again")
    return out


def verify(host, path, hashes, timeout=300):
    """(ok, [paths that differ]) of the copy at PATH on HOST against HASHES."""
    from . import hostrun
    r = hostrun.ssh(host, script=_self_source(), args=["hash", path], timeout=timeout)
    if r.rc != 0:
        return False, [f"hash failed: {r.errtext.strip()[-200:]}"]
    got = r.json().get("files", {})
    some, n = _diff(hashes, got, n=20)
    return n == 0, some


def gc(hosts, root=None, host_root=None, days=7, dry_run=False, timeout=300):
    """t2 copy --gc on each host: {host: {"deleted", "kept", "referenced", "failed", "stale_parts"}}."""
    from . import hostrun
    rr = hostrun.host_path(root or os.environ.get("T2_COPY_ROOT") or DEFAULT_ROOT)
    hr = hostrun.host_path(host_root or os.environ.get("T2_HOST_ROOT") or "/bkoscratch/test2")
    out = {}
    for h in hosts:
        r = hostrun.ssh(h, script=_self_source(), args=["gc", rr, hr, str(days)] + (["--dry-run"] if dry_run else []),
                        timeout=timeout)
        out[h] = r.json() if r.rc == 0 else {"error": r.errtext.strip()[-300:] or f"rc {r.rc}"}
    return out


# --- the committed rule --------------------------------------------------------------------

def head_hashes(checkout, commit="HEAD"):
    """path -> content sha256 at COMMIT (None: absent), for committed_ok."""
    from .keys import GitSource
    src = GitSource(checkout, commit, scope=None)
    return src.sha256


def committed_ok(row, head_hashes):
    """The committed rule (contract 1.5.6): a row counts as a PASS only if its copy
    was committed, or HEAD (HEAD_HASHES: a dict or a function, path -> content
    sha256 or None) has every path of its dirty map with the same content (None:
    absent).  A dirty synced tree's row (dirty null, committed false) never counts."""
    if row.get("committed") is True:
        return True
    d = row.get("dirty")
    if not isinstance(d, dict):
        return False
    get = head_hashes.get if isinstance(head_hashes, dict) else head_hashes
    return all(get(p) == v for p, v in d.items())


if __name__ == "__main__":
    sys.exit(host_main(sys.argv[1:]))
