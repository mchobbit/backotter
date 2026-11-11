#!/usr/bin/env python3
"""ostree's layout, for the test tools that look at an ostree system's files.

Usage: ostree_paths.py default ROOT [BOOT]
         the default deployment of the physical root mounted at ROOT, as a
         path from ROOT (ostree/deploy/STATEROOT/deploy/CHECKSUM.SERIAL);
         exit 1 if there is none.  BOOT is where its /boot is (ROOT/boot)
       ostree_paths.py deployments ROOT
         every deployment, one a line

ROOT is the physical root in fstab's terms (docs/design/bazzite.md, "One tree
convention"): the btrfs subvolume or filesystem that holds ostree/repo and
ostree/deploy, as load's mount stage and vm-mount mount it, with /boot below
it.  Running on a booted ostree system, that is /sysroot (with /boot at /boot).

The default deployment is the one the boot loader starts unless told
otherwise: the BLS entry on /boot with the highest `version` (ostree writes
the default deployment's entry with the highest), its `ostree=` argument
followed through the boot.N symlinks to a deployment.  With no entries, the
only deployment there is, else none.

The etc mapping (bs1-manifest, compare.py): only the default deployment's
etc/ stands for the system's /etc, so only its paths are matched against the
lists of expected differences as etc/...; every other deployment's etc keeps
its own path and gets no exceptions.  EXPECTED and REMADE are what booting an
ostree system changes beyond the lists for any Linux, as logical paths:
measured on kinoite44 (B1's two-boot manifest diff).
"""

from __future__ import annotations

import os
import re
import sys

DEPLOY = "ostree/deploy"

# What booting changes, beyond bs1-manifest's lists for every system (B1:
# nothing more; var/tmp, var/cache, var/lib, var/log and boot/efi/NvVars are
# those lists').
EXPECTED: list[str] = []
# Rewritten at every boot, the same but for the mtime: plasma-setup's
# autologin setting, tuned's state, and systemd's per-slice settings of a
# root login (SSH's).
REMADE = ["etc/plasmalogin.conf.d/*", "etc/tuned/*", "etc/systemd/system.control/*"]

_DEPLOYMENT = re.compile(r"ostree/deploy/[^/]+/deploy/[^/]+\.[0-9]+")


def deployments(root: str) -> list[str]:
    """Every deployment directory under ROOT, as paths from ROOT, sorted."""
    out = []
    base = os.path.join(root, DEPLOY)
    try:
        stateroots = sorted(os.listdir(base))
    except OSError:
        return []
    for s in stateroots:
        d = os.path.join(base, s, "deploy")
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            rel = f"{DEPLOY}/{s}/deploy/{n}"
            if _DEPLOYMENT.fullmatch(rel) and os.path.isdir(os.path.join(d, n)) and not os.path.islink(os.path.join(d, n)):
                out.append(rel)
    return out


def is_ostree(root: str) -> bool:
    return bool(deployments(root))


def resolve(root: str, path: str) -> str | None:
    """PATH inside ROOT, its symlinks followed as the system would follow
    them (an absolute link means ROOT/..., never the host's), as a path from
    ROOT; None if it leads nowhere or loops."""
    parts, done, hops = path.strip("/").split("/"), [], 0
    while parts:
        name = parts.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            done = done[:-1]
            continue
        host = os.path.join(root, *done, name)
        if os.path.islink(host):
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(host)
            if target.startswith("/"):
                done = []
            parts = target.strip("/").split("/") + parts
            continue
        if not os.path.lexists(host):
            return None
        done.append(name)
    return "/".join(done)


def _version_key(v: str) -> tuple:
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in re.findall(r"\d+|[^\d.]+", v))


def bls_entries(boot: str) -> list[dict]:
    """The BLS entries in BOOT/loader/entries (loader is ostree's symlink to
    loader.0 or loader.1), each {"file", "version", "options"}."""
    d = os.path.join(boot, "loader", "entries")
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for n in names:
        if not n.endswith(".conf"):
            continue
        e = {"file": n, "version": "", "options": ""}
        try:
            with open(os.path.join(d, n), encoding="utf-8", errors="replace") as f:
                for line in f:
                    k, _, v = line.strip().partition(" ")
                    if k in ("version", "options"):
                        e[k] = v.strip()
        except OSError:
            continue
        out.append(e)
    return out


def karg(options: str, name: str) -> str | None:
    for w in options.split():
        k, eq, v = w.partition("=")
        if k == name and eq:
            return v
    return None


def default_deployment(root: str, boot: str | None = None) -> str | None:
    """ROOT's default deployment (see the module's doc), as a path from ROOT."""
    deps = deployments(root)
    if not deps:
        return None
    entries = [e for e in bls_entries(boot or os.path.join(root, "boot")) if karg(e["options"], "ostree")]
    if entries:
        top = max(entries, key=lambda e: _version_key(e["version"]))
        rel = resolve(root, karg(top["options"], "ostree"))
        return rel if rel in deps else None
    return deps[0] if len(deps) == 1 else None


def default_from_rows(links: dict[str, str], dirs: set[str]) -> str | None:
    """The default deployment from a listing of the tree (bs1-manifest's,
    with no record of it): LINKS maps each symlink's path to its target,
    DIRS holds the directories.  The only deployment; else, through
    /boot/loader's loader.N to ostree/boot.N, the deployment that link 0
    leads to when all deployments share one boot checksum (index 0 is then
    the default's).  None when that can't tell."""
    deps = sorted(d for d in dirs if _DEPLOYMENT.fullmatch(d))
    if len(deps) <= 1:
        return deps[0] if deps else None
    loader = links.get("boot/loader", "")
    m = re.fullmatch(r"loader\.([01])", loader)
    if not m:
        return None
    bootn = f"ostree/boot.{m[1]}"
    target = links.get(bootn)
    if target is None or "/" in target:
        return None
    base = f"ostree/{target}"
    zeros = [p for p in links if re.fullmatch(re.escape(base) + r"/[^/]+/[^/]+/0", p)]
    if len(zeros) != 1:
        return None
    rel = os.path.normpath(os.path.join(os.path.dirname(zeros[0]), links[zeros[0]]))
    return rel if rel in deps else None


def logical(rel: str, default: str | None) -> str:
    """REL (a path from the physical root) as the system names it, for the
    lists of expected differences: the default deployment's etc is etc."""
    if default:
        etc = default + "/etc"
        if rel == etc or rel.startswith(etc + "/"):
            return "etc" + rel[len(etc):]
    return rel


def main(argv: list[str]) -> int:
    if len(argv) in (2, 3) and argv[0] == "default":
        d = default_deployment(argv[1], argv[2] if len(argv) == 3 else None)
        if d is None:
            return 1
        print(d)
        return 0
    if len(argv) == 2 and argv[0] == "deployments":
        for d in deployments(argv[1]):
            print(d)
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
