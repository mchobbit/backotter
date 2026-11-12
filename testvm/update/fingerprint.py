#!/usr/bin/env python3
"""A guest's fingerprint: what an image is, as far as the backup tests care.

Usage: fingerprint.py DUMP PKGSET
       prints the record (below) as JSON; exit 3, and why on stderr, when
       there is no fingerprint to give

DUMP is `bko-holt dump` of the booted guest, PKGSET what update/pkgset.sh
printed.  vm-update loads this file and writes the result beside image-state
as vms/GUEST/image-fingerprint.json.

A new generation of an image needn't change what SticTest2 and HoltTest2
check, so each has a digest of its own over what it does depend on:

  holt.digest   the kernel (exact), the boot and storage packages' versions,
                and the disk layout
  stic.digest   the kernel line, ZFS's or bcachefs-tools' minor, the LSM
                policy's major, the minors of the fixture's mkfs and attr
                tools; on systems whose tools come with the base system, the
                release stands for them
  stic.fine     those and the exact versions: a patch moves only this

What each field reads differs per kind of system: read_facts() below.  A listing
that said `unknown`, or a field with no line behind it, gives no fingerprint
at all, and the guest's generation stands in for it, which is always safe.

The layout is a digest of judge.py's own view of the dump (which fields it
compares), less what it says may differ between two boots and less anything
holding a version.  So it moves when judge.py's rules or the dump's fields do,
though the guest didn't: `rules`, the sha256 of this file's and judge.py's
sums, tells, and `vm-update --reproject` recomputes the digests from the stored
dumps.  A digest that moved so keeps its old value in `was`.

judge.py is loaded by path, read only: it is an input of every holt cell.
"""

import hashlib
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JUDGE = os.path.join(os.path.dirname(HERE), "holttest1d", "judge.py")

# holt/src/boot.rs's PACKAGES (tests/test_vm_update.py checks that they agree).
PACKAGES = (
    "grub-pc", "grub-pc-bin", "grub-efi-amd64", "grub-efi-amd64-bin", "grub-efi-amd64-signed", "grub-efi-ia32",
    "grub-efi-ia32-bin", "grub-efi-arm64", "grub-efi-arm64-signed", "grub2-common", "grub-common", "shim-signed",
    "shim-unsigned", "shim-helpers-amd64-signed", "mokutil", "efibootmgr", "systemd-boot", "systemd-boot-efi",
    "systemd-ukify", "refind", "syslinux", "extlinux", "syslinux-efi", "os-prober", "linux-image-amd64",
    "linux-image-cloud-amd64", "linux-image-rt-amd64", "initramfs-tools", "dracut", "tiny-initramfs", "mdadm",
    "lvm2", "thin-provisioning-tools", "cryptsetup", "cryptsetup-initramfs", "cryptsetup-bin", "clevis-luks",
    "systemd-cryptsetup", "tpm2-tools", "zfsutils-linux", "zfs-initramfs", "zfs-dracut", "zfs-dkms", "btrfs-progs",
    "xfsprogs", "e2fsprogs", "dosfstools", "exfatprogs", "ntfs-3g", "jfsutils", "f2fs-tools", "nilfs-tools",
    "bcachefs-tools",
    "grub", "grub2-pc", "grub2-efi-x64", "grub2-tools", "shim-x64", "mkinitcpio", "linux", "linux-lts", "kernel-core",
    "zfs-utils", "selinux-policy-targeted",
    "grub-bios", "grub-efi", "mkinitfs", "linux-virt",
    "kernel-install-for-dracut", "eos-dracut", "mhwd",
    "limine", "limine-mkinitcpio-hook", "limine-snapper-sync", "grub2", "grub2-i386-pc", "grub2-x86_64-efi",
    "kernel-default", "snapper", "linux-base",
    "multipath-tools", "dmraid", "nvme-cli", "open-iscsi",
    "grub-i386-pc", "grub-x86_64-efi", "linux-stable", "linux-lts-zfs-bin", "linux-stable-zfs-bin", "zfs", "zfs-ckms", "ckms",
    "cryptsetup-scripts",
    "gentoo-kernel-bin", "gentoo-kernel", "gentoo-sources", "installkernel", "genkernel", "ugrd", "systemd-utils", "zfs-kmod",
)
# Of those, what handles storage; the rest is boot.
STORAGE = frozenset((
    "mdadm", "lvm2", "thin-provisioning-tools", "cryptsetup", "cryptsetup-initramfs", "cryptsetup-bin", "clevis-luks",
    "systemd-cryptsetup", "tpm2-tools", "zfsutils-linux", "zfs-initramfs", "zfs-dracut", "zfs-dkms", "zfs-utils",
    "btrfs-progs", "xfsprogs", "e2fsprogs", "dosfstools", "exfatprogs", "ntfs-3g", "jfsutils", "f2fs-tools",
    "nilfs-tools", "bcachefs-tools", "snapper", "multipath-tools", "dmraid", "nvme-cli", "open-iscsi",
    "zfs", "zfs-ckms", "linux-lts-zfs-bin", "linux-stable-zfs-bin", "cryptsetup-scripts", "zfs-kmod",
))
FIXTURE = ("e2fsprogs", "xfsprogs", "btrfs-progs", "f2fs-tools", "jfsutils", "nilfs-tools", "dosfstools", "attr", "acl")
# Another distribution's name for a fixture tool.
FIXTURE_ALIASES = {"nilfs-utils": "nilfs-tools", "attr-progs": "attr", "acl-progs": "acl", "btrfsprogs": "btrfs-progs"}
ZFS_NAMES = ("zfsutils-linux", "zfs-utils", "zfs", "zfs-user", "zfs-dkms")
LSM_NAMES = ("selinux-policy-targeted", "selinux-policy", "apparmor")

LINUX_KINDS = ("dpkg", "rpm", "pacman", "apk", "xbps", "moss", "portage", "nixos")
BASE_KINDS = ("freebsd", "hardenedbsd", "netbsd", "alpinebsd", "openbsd", "dragonfly", "illumos", "haiku")


class NoFingerprint(Exception):
    """There is no fingerprint to give, and why."""


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rules_hash(this=None, judge=None):
    """The sha256 of the two files' sha256 hex, this file's first."""
    return hashlib.sha256((file_sha(this or os.path.abspath(__file__)) + file_sha(judge or JUDGE)).encode()).hexdigest()


def load_judge(path=None):
    spec = importlib.util.spec_from_file_location("bko_fingerprint_judge", path or JUDGE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- pkgset.sh's listing ---------------------------------------------------------------------

class Listing:
    def __init__(self):
        self.pkgs = {}          # name: {versions}
        self.nix = {}           # name: {versions}
        self.base = {}          # name: value
        self.syspatch = []
        self.nix_system = None
        self.unknown = None     # why, when it is

    def version(self, names):
        """The version(s) of the first of NAMES listed, or None."""
        for n in names:
            if n in self.all:
                return ",".join(sorted(self.all[n]))
        return None

    @property
    def all(self):
        return self.nix if self.kind == "nixos" else self.pkgs

    @property
    def kind(self):
        return self.base.get("pm")


def parse_pkgset(text):
    ls = Listing()
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        t = p[0]
        if t == "unknown":
            ls.unknown = " ".join(p[1:]) or "the listing says unknown"
        elif t == "pkg" and len(p) >= 3:
            ls.pkgs.setdefault(p[1], set()).add(p[2])
        elif t == "nix-pkg" and len(p) >= 3:
            ls.nix.setdefault(p[1], set()).add(p[2])
        elif t == "base" and len(p) >= 3:
            ls.base[p[1]] = " ".join(p[2:])
        elif t == "nix-system" and len(p) == 2:
            ls.nix_system = p[1]
        elif t == "syspatch" and len(p) == 2:
            ls.syspatch.append(p[1])
        else:
            ls.unknown = "a line it can't read: " + line[:80]
    if not text.strip():
        ls.unknown = "the listing is empty"
    elif ls.unknown is None and ls.kind is None:
        ls.unknown = "no `base pm` line"
    return ls


# --- Versions ---------------------------------------------------------------------------------

def minor(v):
    """MAJOR.MINOR of a package version, past any epoch; the first word of one
    with no such start."""
    if v is None:
        return None
    out = []
    for one in str(v).split(","):
        one = re.sub(r"^\d+:", "", one)
        m = re.match(r"v?(\d+)\.(\d+)", one)
        out.append(f"{m[1]}.{m[2]}" if m else re.split(r"[-_+]", one, maxsplit=1)[0])
    return ",".join(sorted(set(out)))


def major(v):
    if v is None:
        return None
    m = re.match(r"(?:\d+:)?(\d+)", str(v))
    return m[1] if m else str(v)


def major_minor(release):
    m = re.search(r"(\d+)\.(\d+)", release or "")
    return f"{m[1]}.{m[2]}" if m else None


# A kernel's builder, USER at HOST and :/path (DragonFly's and NetBSD's version strings).
USER_HOST = re.compile(r"\s*\S+@[^\s:]+(:\S*)?")


def uses_zfs(dump):
    z = (dump.get("storage") or {}).get("zfs") or {}
    return bool(z.get("pools_from_labels") or (z.get("tools") or {}).get("imported_pools"))


def need(value, what):
    if not value:
        raise NoFingerprint(f"nothing to read {what} from")
    return value


def read_facts(dump, ls):
    """facts (less layout) of one guest: contract section 1.5's table."""
    kind = ls.kind
    os_ = dump.get("os") or {}
    release, version = os_.get("kernel_release"), os_.get("kernel_version")
    f = {"os_kind": kind, "bcachefs": None, "zfs": None, "lsm": {}, "fixture": {}, "boot": {}, "storage": {}}
    if kind in LINUX_KINDS:
        f["kernel"] = need(release, "the kernel (os.kernel_release)")
        f["kernel_line"] = need(major_minor(release), "the kernel line")
        for name in PACKAGES:
            v = ls.version([name])
            if v is not None:
                f["storage" if name in STORAGE else "boot"][name] = v
        need(f["boot"] or f["storage"], "the boot and storage packages")
        f["zfs"] = ls.version(ZFS_NAMES)
        f["bcachefs"] = ls.version(["bcachefs-tools"])
        f["lsm"] = {n: ls.version([n]) for n in LSM_NAMES if ls.version([n]) is not None}
        for name in sorted(ls.all):
            canonical = FIXTURE_ALIASES.get(name, name)
            if canonical in FIXTURE and canonical not in f["fixture"]:
                f["fixture"][canonical] = ls.version([name])
        need(f["fixture"], "the fixture's tools")
    elif kind in BASE_KINDS:
        base = {k: v for k, v in ls.base.items() if k not in ("pm", "zfs")}
        if kind in ("freebsd", "hardenedbsd"):
            f["kernel_line"] = need(major_minor(release), "the release")
            if kind == "freebsd":
                f["kernel"] = need(version, "the kernel (os.kernel_version)")
                for k in ("kernel", "libc", "freebsd-version-k", "freebsd-version-u"):
                    need(base.get(k), f"the base system ({k})")
                f["boot"] = dict(base)
                for n in sorted(ls.pkgs):
                    if n.startswith(("FreeBSD-", "GhostBSD-")):
                        f["boot"][n] = ls.version([n])
            else:
                f["kernel"] = need(base.get("kernel"), "the kernel (its sha256)")
                f["boot"] = dict(base)
            f["zfs"] = ls.base.get("zfs")
        elif kind in ("netbsd", "alpinebsd"):
            f["kernel"] = need(USER_HOST.sub("", version or "").strip(), "the kernel (os.kernel_version)")
            f["kernel_line"] = need(release, "the release")
            if kind == "netbsd":
                need(base.get("netbsd-daily"), "the base system (the daily's sha512)")
                f["boot"] = dict(base)
            else:
                f["boot"] = {n: ls.version([n]) for n in sorted(ls.pkgs) if n.startswith("netbsd-")}
                need(f["boot"], "the base system (netbsd-* packages)")
        elif kind == "openbsd":
            f["kernel"] = need(version, "the kernel (os.kernel_version)")
            f["kernel_line"] = need(release, "the release")
            need(base.get("syspatch-count"), "the base system (syspatch -l)")
            f["boot"] = {"syspatch": sorted(ls.syspatch)}
        elif kind == "dragonfly":
            f["kernel"] = need(USER_HOST.sub("", version or "").strip(), "the kernel (os.kernel_version)")
            f["kernel_line"] = need(major_minor(release), "the release")
            need(base.get("bko-base"), "the base system (/var/db/bko-base)")
            f["boot"] = dict(base)
        elif kind == "illumos":
            f["kernel"] = need(version, "the kernel (os.kernel_version)")
            f["kernel_line"] = need(re.sub(r"[a-z]+$", "", os_.get("version_id") or ""), "the release")
            f["boot"] = {n: ls.version([n]) for n in sorted(ls.pkgs) if
                         n == "entire" or n.startswith(("system/kernel", "system/boot/", "system/file-system/"))
                         or "osnet" in n}
            need(f["boot"], "the system packages")
            f["zfs"] = ls.version(["system/file-system/zfs"])
        elif kind == "haiku":
            f["kernel"] = need(version, "the kernel (os.kernel_version)")
            f["kernel_line"] = need(release, "the release")
            f["boot"] = {n: ls.version([n]) for n in ("haiku", "haiku_loader", "haiku_datatranslators")
                         if ls.version([n]) is not None}
            need(f["boot"], "the system packages")
        # The base system is the fixture's tools.  Exact here, for stic.fine:
        # its digest takes the release for them.
        f["fixture"] = {"base": sha(f["boot"])}
    else:
        raise NoFingerprint(f"a kind of system it doesn't know: {kind}")
    if uses_zfs(dump):
        need(f["zfs"], "the ZFS version")
    return f


# --- The layout -------------------------------------------------------------------------------

# Top-level fields that are the machine's, the run's or the tool's, and
# versions.  boot.* holds versions and loader files that any update rewrites.
# storage.backup_sources, storage.physical_root and a mount's alias_of are
# worked out from the mounts and fstab, which the layout already has
# (docs/design/bazzite.md, B2): a dump that first carries them is the same
# layout.
LAYOUT_DROP = re.compile(r"^(generated|format|schema|privileged|warnings|host\.|tool\.|os\.|_illumos\."
                         r"|storage\.backup_sources\.|storage\.physical_root$|storage\.mounts\.\d+\.alias_of$)")
LAYOUT_BOOT_KEEP = re.compile(r"^boot\.loader\.(name|firmware)$")


def _boot_order(v):
    """Sort, in place, the lists that hold mounts in the order this boot
    mounted them (systemd mounts in parallel: a tumbleweed's snapshot
    subvolumes came up in another order from one run to the next)."""
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "mounted_at" and isinstance(x, list):
                x.sort(key=str)
            elif k == "mounted_subvolumes" and isinstance(x, list):
                x.sort(key=canon)
            else:
                _boot_order(x)
    elif isinstance(v, list):
        for x in v:
            _boot_order(x)


def layout(dump, judge=None):
    """A digest of judge.py's view of DUMP, less what may differ between two
    boots of one image (IGNORED and its pairwise rules) and any version."""
    j = judge or load_judge()
    d = j.md_by_name(j.illumos_normalise(json.loads(canon(dump))))
    st = d.get("storage") or {}
    # As judge.py's main: lists in the boot's own order.
    if isinstance(st.get("swaps"), list):
        st["swaps"].sort(key=lambda w: w.get("file") or "")
    if isinstance(st.get("mounts"), list):
        st["mounts"].sort(key=lambda m: (m.get("target") or "", m.get("source") or ""))
    _boot_order(d)
    flat = {}
    j.flatten(d, "", flat)
    view = {}
    for k, v in flat.items():
        if LAYOUT_DROP.search(k) or (k.startswith("boot") and not LAYOUT_BOOT_KEEP.search(k)):
            continue
        if any(re.search(p, k) for p, _ in j.IGNORED):
            continue
        if k.endswith(".md.array_state"):
            continue
        if k.endswith(".inodes") and flat.get(k[: -len("inodes")] + "type") == "xfs":
            continue
        m = re.match(r"^(storage\.other_devices\.\d+)\.", k)
        if m and flat.get(m.group(1) + ".kind") == "zram":
            continue
        if isinstance(v, str) and v.startswith("/dev/disk/by-diskseq/"):
            continue
        if re.search(r"\.features\.[a-z_]+$", k) and v in ("enabled", "active"):
            v = "enabled (or active)"
        view[k] = v
    return sha(view)


# --- The digests ------------------------------------------------------------------------------

def holt_digest(f):
    return sha({k: f[k] for k in ("kernel", "boot", "storage", "layout")})


def stic_view(f):
    base = f["os_kind"] in BASE_KINDS
    return {
        "kernel_line": f["kernel_line"],
        "zfs": minor(f["zfs"]),
        "bcachefs": minor(f["bcachefs"]),
        "lsm": {k: major(v) for k, v in f["lsm"].items()},
        "fixture": {"base": f["kernel_line"]} if base else {k: minor(v) for k, v in f["fixture"].items()},
    }


def stic_digest(f):
    return sha(stic_view(f))


def fine_digest(f):
    return sha({"stic": stic_view(f), "kernel": f["kernel"], "zfs": f["zfs"], "bcachefs": f["bcachefs"],
                "lsm": f["lsm"], "fixture": f["fixture"]})


def build(dump, pkgset, judge=None):
    """The record for DUMP (parsed) and PKGSET (text), or NoFingerprint."""
    ls = parse_pkgset(pkgset)
    if ls.unknown:
        raise NoFingerprint("the package listing is unknown: " + ls.unknown)
    f = read_facts(dump, ls)
    f["layout"] = layout(dump, judge)
    return {
        "facts": f,
        "holt": {"digest": holt_digest(f)},
        "stic": {"digest": stic_digest(f), "fine": fine_digest(f)},
        "tool": (dump.get("tool") or {}).get("version"),
        "rules": rules_hash(),
    }


def sidecar(rec, generation, source, t, previous=None):
    """image-fingerprint.json for a new write: `was` carries over from
    PREVIOUS (the sidecar this replaces) when the digest is unchanged, and is
    emptied otherwise."""
    prev = previous or {}

    def was(family):
        p = prev.get(family) or {}
        return list(p.get("was") or []) if p.get("digest") == rec[family]["digest"] else []

    return {
        "v": 1, "generation": generation, "source": source, "t": t, "tool": rec["tool"], "rules": rec["rules"],
        "facts": rec["facts"],
        "holt": {"digest": rec["holt"]["digest"], "was": was("holt")},
        "stic": {"digest": rec["stic"]["digest"], "fine": rec["stic"]["fine"], "was": was("stic")},
    }


def reproject(old, dump, pkgset, judge=None):
    """OLD, the sidecar of a stored dump, with its digests recomputed by
    today's rules.  A digest that moved goes in `was`."""
    rec = build(dump, pkgset, judge)

    def was(family):
        p = old.get(family) or {}
        w = list(p.get("was") or [])
        if p.get("digest") and p["digest"] != rec[family]["digest"] and p["digest"] not in w:
            w.append(p["digest"])
        return w

    new = dict(old, tool=rec["tool"], rules=rec["rules"], facts=rec["facts"])
    new["holt"] = {"digest": rec["holt"]["digest"], "was": was("holt")}
    new["stic"] = {"digest": rec["stic"]["digest"], "fine": rec["stic"]["fine"], "was": was("stic")}
    return new


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as f:
        dump = json.load(f)
    with open(argv[1], encoding="utf-8") as f:
        pkgset = f.read()
    try:
        rec = build(dump, pkgset)
    except NoFingerprint as e:
        print(f"fingerprint.py: no fingerprint: {e}", file=sys.stderr)
        return 3
    json.dump(rec, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
