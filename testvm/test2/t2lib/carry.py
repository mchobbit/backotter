"""The carry rule for bin-only changes (contract 1.6.4, rulings R14, R15), and the git
diffs the planner needs: changed paths since a row's tree (R5's unmapped paths, the holt
code diff, golden plans).

For every changed binary of a cell one of these must hold, else nothing is carried:
- version-only: the fork diff from the PASS's commits to the current ones changes only
  *.md and version lines (restic internal/global/global.go `Version = "..."`, Cargo.toml's
  [package] version with the matching Cargo.lock entry); bko-holt: the holt code diff
  changes only those Cargo lines (617c4de, 620ec60);
- affected-v1: testvm/compare/affected.sh over the three fork ranges prints platforms
  that exclude the binary's OS, and its .digest lines other than the sources are unchanged
  (haiku never: affected.sh has no Haiku);
- dfly-image: dragonfly/rustic-bko's .digest differs only in its build image (sysroot) line;
- rustic-panel and plan-diff: HoltTest2's, decided in plan.py.
A binary whose sha256 changed while its source commits didn't is never carried, except
under dfly-image.  Everything here reads only (git, affected.sh); nothing is written.
"""

import hashlib
import os
import re
import subprocess

VERSION_LINE = re.compile(r'^[-+]\s*(const|var)?\s*Version\s*=\s*"[^"]*"')
_VERSION_STMT = re.compile(r'^(\s*(?:const|var)?\s*Version\s*=\s*)"[^"]*"', re.M)
_TOML_SECTION = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
_TOML_VERSION = re.compile(r'^(\s*version\s*=\s*)"[^"]*"(.*)$')
_TOML_NAME = re.compile(r'^\s*name\s*=\s*"([^"]*)"')
EXCLUDE_DIRS = ("__pycache__/",)
EXCLUDE_SUFFIX = (".pyc",)
EXCLUDE_NAMES = (".DS_Store",)
FORKS = ("restic", "rustic_core", "rustic")
# affected.sh's platforms -> the binaries' OS directories (contract 1.6.4)
AFFECTED_OS = {"linux": ("linux",), "sparse": ("linux",), "zfsguests": ("linux",), "dragonfly": ("dragonfly",),
               "openbsd_illumos": ("openbsd", "illumos", "omnios"), "freebsd": ("freebsd",), "netbsd": ("netbsd",)}
# .digest lines that come from the sources (the rest: toolchain, profile, scripts, build image)
SOURCE_LINES = ("sources ", "tree ", "digest ")
BUILT_LINES = ("built on ", "reused from ", "reused by ")
IMAGE_LINES = ("sysroot ", "image ")


def excluded(path):
    """The copy's exclusions (contract 1.3): __pycache__/, *.pyc, .DS_Store, holt/target/."""
    base = path.rsplit("/", 1)[-1]
    return (any(d in path + "/" for d in EXCLUDE_DIRS) or path.endswith(EXCLUDE_SUFFIX) or base in EXCLUDE_NAMES
            or path.startswith("holt/target/") or path == "holt/target")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


class Git:
    """Read-only git in one repository, with a cache of blobs by (rev, path)."""

    def __init__(self, repo):
        self.repo = repo
        self._blob = {}
        self._rev = {}

    def run(self, *args, check=True):
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"git -C {self.repo} {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
        return r

    def rev(self, name):
        """NAME's commit; a full hex name is looked up once (a commit never changes, a ref may)."""
        fixed = bool(re.fullmatch(r"[0-9a-f]{40}", name or ""))
        if fixed and name in self._rev:
            return self._rev[name]
        r = self.run("rev-parse", "--verify", "-q", name + "^{commit}", check=False)
        out = r.stdout.decode().strip() if r.returncode == 0 else None
        if fixed:
            self._rev[name] = out
        return out

    def show(self, rev, path):
        """The bytes of PATH at REV, or None when it isn't there."""
        k = (rev, path)
        if k not in self._blob:
            r = self.run("cat-file", "blob", f"{rev}:{path}", check=False)
            self._blob[k] = r.stdout if r.returncode == 0 else None
        return self._blob[k]

    def names(self, old, new=None, roots=()):
        """Paths that differ between OLD and NEW (None: the working tree, untracked files too)."""
        args = ["diff", "--no-renames", "--name-only", "-z", old] + ([new] if new else []) + ["--"] + list(roots)
        out = set(p for p in self.run(*args).stdout.decode(errors="surrogateescape").split("\0") if p)
        if new is None:
            r = self.run("ls-files", "-z", "--others", "--exclude-standard", "--", *roots)
            out |= set(p for p in r.stdout.decode(errors="surrogateescape").split("\0") if p)
        return sorted(out)

    def worktree(self, path):
        try:
            with open(os.path.join(self.repo, path), "rb") as f:
                return f.read()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None


def changed_since(git, head, old_commit, old_dirty, roots, keep=None, wt=None):
    """Repo paths under ROOTS whose working-tree content differs from what a row ran: the
    content at OLD_COMMIT, or for the row's dirty paths HEAD's (the committed rule makes them
    equal, contract 1.5.6).  Returns {path: (old_sha256 or None, new_sha256 or None)}.
    KEEP(path), when given, filters the candidates before any blob is read.  WT, when given,
    is git.names(HEAD, None, ROOTS) worked out once: then only OLD_COMMIT..HEAD is diffed."""
    dirty = old_dirty or {}
    cand = {p for p in dirty if any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)}
    if wt is not None:
        cand |= set(wt)
        if head != old_commit:
            cand |= set(git.names(old_commit, head, roots))
    else:
        cand |= set(git.names(old_commit, None, roots))
        if head != old_commit:
            cand |= set(git.names(head, None, roots))
    out = {}
    for p in sorted(cand):
        if excluded(p) or (keep is not None and not keep(p)):
            continue
        if p in dirty:
            ob = git.show(head, p) if dirty[p] is not None else None
        else:
            ob = git.show(old_commit, p)
        nb = git.worktree(p)
        o = sha256_bytes(ob) if ob is not None else None
        n = sha256_bytes(nb) if nb is not None else None
        if o != n:
            out[p] = (o, n)
    return out


def old_content(git, head, old_commit, old_dirty, path):
    if path in (old_dirty or {}):
        return git.show(head, path) if old_dirty[path] is not None else None
    return git.show(old_commit, path)


# --- version-only --------------------------------------------------------------------

def _norm_go_version(text):
    return _VERSION_STMT.sub(r'\1"X"', text)


def toml_package_names(text):
    """The [package] name of a Cargo.toml."""
    sec, names = None, []
    for line in text.splitlines():
        m = _TOML_SECTION.match(line)
        if m:
            sec = m.group(1).strip()
            continue
        if sec == "package":
            n = _TOML_NAME.match(line)
            if n:
                names.append(n.group(1))
    return names


def _norm_toml_package_version(text):
    out, sec = [], None
    for line in text.splitlines():
        m = _TOML_SECTION.match(line)
        if m:
            sec = m.group(1).strip()
        elif sec == "package" and _TOML_VERSION.match(line):
            line = _TOML_VERSION.sub(r'\1"X"\2', line)
        out.append(line)
    return "\n".join(out)


def _norm_lock_versions(text, names):
    """Cargo.lock with the version line of each [[package]] named in NAMES made "X"."""
    out, cur = [], None
    for line in text.splitlines():
        if line.strip() == "[[package]]":
            cur = None
        n = _TOML_NAME.match(line)
        if n:
            cur = n.group(1)
        elif cur in names and _TOML_VERSION.match(line):
            line = _TOML_VERSION.sub(r'\1"X"\2', line)
        out.append(line)
    return "\n".join(out)


def version_only_files(changes, read_old, read_new):
    """CHANGES: paths whose content differs.  read_old/read_new(path) -> bytes or None.
    True when every change is *.md, a Go `Version = "..."` line in internal/global/global.go,
    a Cargo.toml [package] version, or that package's version in Cargo.lock."""
    names = set()
    for p in changes:
        if p.rsplit("/", 1)[-1] == "Cargo.toml":
            for b in (read_old(p), read_new(p)):
                if b is not None:
                    names |= set(toml_package_names(b.decode("utf-8", "replace")))
    bad = []
    for p in sorted(changes):
        if p.endswith(".md"):
            continue
        o, n = read_old(p), read_new(p)
        if o is None or n is None:
            bad.append(f"{p} {'added' if o is None else 'deleted'}")
            continue
        o, n = o.decode("utf-8", "replace"), n.decode("utf-8", "replace")
        base = p.rsplit("/", 1)[-1]
        if p.endswith("internal/global/global.go"):
            same = _norm_go_version(o) == _norm_go_version(n)
        elif base == "Cargo.toml":
            same = _norm_toml_package_version(o) == _norm_toml_package_version(n)
        elif base == "Cargo.lock":
            same = bool(names) and _norm_lock_versions(o, names) == _norm_lock_versions(n, names)
        else:
            same = False
        if not same:
            bad.append(p)
    return (not bad), bad


def fork_version_only(bko_git, old, new, repos):
    """OLD, NEW: {repo: commit}.  True when each of REPOS whose commit moved changed only
    *.md and version lines between them.  Returns (ok, why)."""
    moved = []
    for repo in repos:
        o, n = old.get(repo), new.get(repo)
        if not o or not n:
            return False, f"{repo}: commit unknown ({o or '?'}..{n or '?'})"
        if o == n:
            continue
        g = Git(os.path.join(bko_git, repo))
        if not g.rev(o) or not g.rev(n):
            return False, f"{repo}: {o[:7]} or {n[:7]} not in {g.repo}"
        changes = g.names(o, n)
        ok, bad = version_only_files(changes, lambda p: g.show(o, p), lambda p: g.show(n, p))
        if not ok:
            return False, f"{repo} {o[:7]}..{n[:7]} changes {', '.join(bad[:3])}" + (" ..." if len(bad) > 3 else "")
        moved.append(f"{repo} {o[:7]}->{n[:7]}")
    if not moved:
        return False, "no fork commit moved"
    return True, "version lines only: " + ", ".join(moved)


def holt_code_paths(paths):
    """The paths CODE covers (contract 2.3): holt/** less holt/target/, holt/tests/ and *.md."""
    return [p for p in paths if p.startswith("holt/") and not p.startswith(("holt/target/", "holt/tests/"))
            and not p.endswith(".md")]


def holt_code_digest(files):
    """CODE from {path: sha256} of holt/**: sha256 over sorted `path\\tsha256` lines of the code
    paths.  (P5 builds by it; the key's `code:CODE` comes from keys.inputs.)"""
    lines = "".join(f"{p}\t{files[p]}\n" for p in sorted(holt_code_paths(files)))
    return hashlib.sha256(lines.encode()).hexdigest()


# --- .digest texts ----------------------------------------------------------------------

def digest_lines(text, drop):
    """The lines of a .digest text, less built-on/reused lines and those starting with DROP."""
    out = []
    for line in (text or "").splitlines():
        s = line.rstrip()
        if not s or s.startswith(BUILT_LINES) or s.startswith(tuple(drop)):
            continue
        out.append(s)
    return out


def non_source_same(old_text, new_text):
    """True when the .digest lines other than the sources' (toolchain, profile, scripts,
    build image) are unchanged."""
    return digest_lines(old_text, SOURCE_LINES) == digest_lines(new_text, SOURCE_LINES)


def dfly_image_only(old_text, new_text):
    """True when dragonfly/rustic-bko's .digest differs only in its build image line."""
    if not old_text or not new_text:
        return False
    o, n = digest_lines(old_text, ("digest ",)), digest_lines(new_text, ("digest ",))
    if o == n:
        return False
    return [l for l in o if not l.startswith(IMAGE_LINES)] == [l for l in n if not l.startswith(IMAGE_LINES)]


# --- affected.sh v1 ------------------------------------------------------------------------

def affected_v1(checkout, bko_git, old, new, timeout=120):
    """testvm/compare/affected.sh over the forks' ranges: the set of platforms, or (None, why)."""
    args = [os.path.join(checkout, "testvm/compare/affected.sh")]
    flag = {"restic": "--restic", "rustic_core": "--rustic-core", "rustic": "--rustic"}
    for repo in FORKS:
        o, n = old.get(repo), new.get(repo)
        if not o or not n:
            return None, f"{repo}: commit unknown"
        if o != n:
            args += [flag[repo], f"{o}..{n}"]
    if len(args) == 1:
        return set(), "no fork commit moved"
    env = dict(os.environ, RESTIC_REPO=os.path.join(bko_git, "restic"),
               RUSTIC_CORE_REPO=os.path.join(bko_git, "rustic_core"), RUSTIC_REPO=os.path.join(bko_git, "rustic"))
    try:
        r = subprocess.run(["bash"] + args, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"affected.sh: {e}"
    if r.returncode != 0:
        return None, f"affected.sh exit {r.returncode}: {r.stderr.strip()[:160]}"
    line = r.stdout.splitlines()[0] if r.stdout.strip() else ""
    return set(line.split()), "affected.sh says " + (line or "nothing")


def affected_oses(platforms):
    out = set()
    for p in platforms:
        out |= set(AFFECTED_OS.get(p, ("linux", "freebsd", "netbsd", "openbsd", "illumos", "omnios", "dragonfly")))
    return out


# --- the rule for one archiver binary ---------------------------------------------------------

def binary_repos(name):
    """OS/restic-bko -> (restic,); OS/rustic-bko -> (rustic, rustic_core)."""
    return ("restic",) if name.endswith("restic-bko") else ("rustic", "rustic_core")


class ArchiverCarry:
    """Decides the carry rule for archiver binaries, caching the git and affected.sh work.

    old/new commits: {repo: 40-hex} for the three forks, from a PASS's meta.bin and from the
    current round's commit files (or BKO_GIT's heads for a round not built yet)."""

    def __init__(self, checkout, bko_git):
        self.checkout, self.bko_git = checkout, bko_git
        self._vo, self._aff = {}, {}

    def version_only(self, old, new, repos):
        k = (tuple(sorted(old.items())), tuple(sorted(new.items())), tuple(repos))
        if k not in self._vo:
            self._vo[k] = fork_version_only(self.bko_git, old, new, repos)
        return self._vo[k]

    def affected(self, old, new):
        k = (tuple(sorted(old.items())), tuple(sorted(new.items())))
        if k not in self._aff:
            self._aff[k] = affected_v1(self.checkout, self.bko_git, old, new)
        return self._aff[k]

    def binary(self, name, old, new, old_digest, new_digest, old_all=None, new_all=None):
        """NAME OS/restic-bko or OS/rustic-bko; OLD, NEW its own source commits ({repo: hex});
        OLD_ALL, NEW_ALL the three forks' (affected.sh runs over all three ranges).  Returns
        (rule, why): rule a carry:... name, or None with why it doesn't hold."""
        osd = name.split("/", 1)[0]
        repos = binary_repos(name)
        moved = [r for r in repos if old.get(r) and new.get(r) and old[r] != new[r]]
        unknown = [r for r in repos if not old.get(r) or not new.get(r)]
        if name == "dragonfly/rustic-bko" and not moved and not unknown and dfly_image_only(old_digest, new_digest):
            return "carry:dfly-image", "dragonfly/rustic-bko: only its build image changed"
        if unknown:
            return None, f"{name}: source commits unknown ({', '.join(unknown)})"
        if not moved:
            return None, f"{name} changed while its source commits didn't (toolchain, profile or build script)"
        known = old_digest is not None and new_digest is not None
        if known and not non_source_same(old_digest, new_digest):
            return None, f"{name}: its .digest's toolchain, profile, script or image lines changed too"
        ok, why = self.version_only(old, new, repos)
        if ok:
            return "carry:version-only", why
        if osd == "haiku":
            return None, f"{name}: haiku is never carried by affected.sh (it has no Haiku); {why}"
        if not known:
            return None, f"{name}: no .digest text to compare its non-source lines; {why}"
        plats, awhy = self.affected(dict(old_all or {}, **old), dict(new_all or {}, **new))
        if plats is None:
            return None, f"{name}: {awhy}"
        if osd in affected_oses(plats):
            return None, f"{name}: {awhy}"
        return "carry:affected-v1", awhy
