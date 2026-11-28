#!/usr/bin/env python3
"""list-install.py DISK: what a KDE Linux install left on DISK, as text to diff.

Run as root on a Linux with the disk attached and not mounted (the rescue
stick): make-kdelinux.sh --diff lists the by-hand Calamares install and
install.sh's with it, and diffs the two (docs/design/kde-linux.md, K1's
"one GUI install by hand, diffed").  Mounts read-only, under /run, and
unmounts again.  Prints:

  gpt     the partition table (sfdisk -d), the disk's and partitions' UUIDs
          replaced by their roles so two installs compare
  fs      each partition's blkid (TYPE, LABEL, PARTLABEL, PARTTYPE, flags)
  btrfs   the top level's subvolumes (path, read-only, parent), the
          features (simple quotas), qgroups
  tree    every entry of the btrfs top level (into every subvolume, never
          into the image file) and of the ESP: path, type, mode, owner,
          size, link target, xattrs, and the SHA-256 of files up to 1 MiB
          (bigger ones: size only, but the image file's and UKIs' SHA-256)

A LUKS root (Calamares' "Encrypt system") needs --key FILE.
"""
import hashlib, json, os, re, stat, subprocess, sys

def run(*a, check=True):
    return subprocess.run(a, check=check, capture_output=True, text=True).stdout

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()

def main():
    args = sys.argv[1:]
    key = None
    if args[:1] == ["--key"]:
        key, args = args[1], args[2:]
    disk, = args
    out = []
    subs = {}   # uuid -> role, so two installs diff
    def role(u, name):
        subs.setdefault(u.lower(), f"<{name}>")
    dump = run("sfdisk", "-d", disk)
    m = re.search(r"label-id: (\S+)", dump)
    if m: role(m.group(1), "disk-uuid")
    parts = run("lsblk", "-lnpo", "NAME", disk).split()[1:]
    info = {}
    for i, p in enumerate(parts, 1):
        b = {}
        for line in run("blkid", "-p", "-o", "export", p, check=False).splitlines():
            k, _, v = line.partition("=")
            b[k] = v
        info[p] = b
        for k in ("UUID", "PART_ENTRY_UUID", "UUID_SUB"):
            if k in b: role(b[k], f"p{i}-{k.lower()}")
    out.append("== gpt")
    out += [l for l in dump.splitlines() if not l.startswith("device:")]
    out.append("== fs")
    for p, b in info.items():
        out.append(p + " " + " ".join(f"{k}={b[k]}" for k in sorted(b) if k not in ("PART_ENTRY_DISK", "PART_ENTRY_OFFSET", "PART_ENTRY_SIZE", "PART_ENTRY_NUMBER", "DEVNAME")))
    esp = next(p for p, b in info.items() if b.get("PART_ENTRY_TYPE", "").lower() == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
    rootp = next(p for p, b in info.items() if b.get("PART_ENTRY_TYPE", "").lower() == "4f68bce3-e8cd-4db1-96e7-fbcaf984b709")
    dev, opened = rootp, False
    if info[rootp].get("TYPE") == "crypto_LUKS":
        out += ["luks " + l for l in run("cryptsetup", "luksDump", rootp).splitlines()
                if re.match(r"\s*(Version|Cipher|Cipher key|PBKDF|Hash|Sector size|Label|Subsystem|Flags|Keyslots|Tokens)\b", l)]
        run("cryptsetup", "open", "--readonly", "--key-file", key, rootp, "bkolist")
        dev, opened = "/dev/mapper/bkolist", True
        for line in run("blkid", "-p", "-o", "export", dev).splitlines():
            k, _, v = line.partition("=")
            if k in ("UUID", "UUID_SUB"): role(v, f"btrfs-{k.lower()}")
            out.append(f"inner {k}={v}")
    top, espm = "/run/bko-list-top", "/run/bko-list-esp"
    os.makedirs(top, exist_ok=True); os.makedirs(espm, exist_ok=True)
    run("mount", "-o", "ro,subvol=/", dev, top)
    run("mount", "-o", "ro", esp, espm)
    try:
        out.append("== btrfs")
        out += [re.sub(r"ID \d+ gen \d+ cgen \d+ parent \d+ top level \d+ ", "", l)
                for l in run("btrfs", "subvolume", "list", "-a", "-r", "-q", "-u", top).splitlines()]
        out += ["rw: " + re.sub(r"ID \d+ gen \d+ top level \d+ ", "", l)
                for l in run("btrfs", "subvolume", "list", top).splitlines()]
        sup = run("btrfs", "inspect-internal", "dump-super", dev)
        out += [l for l in sup.splitlines() if re.match(r"(incompat_flags|compat_ro_flags|\s+\|)", l)]
        out += ["qgroup " + l for l in run("btrfs", "qgroup", "show", top, check=False).splitlines()[:3]]
        out.append("== tree")
        for base, name in ((top, "top"), (espm, "esp")):
            for d, dirs, files in os.walk(base):
                dirs.sort(); files.sort()
                for n in [""] + dirs + files:
                    p = os.path.join(d, n) if n else d
                    if n == "" and d != base:
                        continue
                    st = os.lstat(p)
                    rel = name + "/" + os.path.relpath(p, base)
                    t = ("d" if stat.S_ISDIR(st.st_mode) else "l" if stat.S_ISLNK(st.st_mode)
                                         else "f" if stat.S_ISREG(st.st_mode) else "c" if stat.S_ISCHR(st.st_mode) else "?")
                    line = f"{rel} {t} {stat.S_IMODE(st.st_mode):04o} {st.st_uid}:{st.st_gid}"
                    if t == "l":
                        line += " -> " + os.readlink(p)
                    elif t == "f":
                        big = st.st_size > (1 << 20)
                        keep = n.endswith(".erofs") or n.endswith(".efi") or n.endswith(".EFI")
                        line += f" {st.st_size}" + ("" if big and not keep else " " + sha(p))
                    try:
                        xs = sorted(os.listxattr(p, follow_symlinks=False))
                    except OSError:
                        xs = []
                    if xs:
                        line += " xattrs=" + ",".join(xs)
                    if t == "d" and base == top:
                        try:
                            if os.stat(p).st_ino == 256:
                                line += " SUBVOLUME"
                        except OSError:
                            pass
                    out.append(line)
                    if t == "f" and n.endswith(".erofs"):
                        la = run("lsattr", "-d", p, check=False).split()
                        out.append(f"{rel} lsattr {la[0] if la else '?'}")
    finally:
        run("umount", top, check=False); run("umount", espm, check=False)
        if opened:
            run("cryptsetup", "close", "bkolist", check=False)
    text = "\n".join(out)
    for u, r in subs.items():
        text = re.sub(re.escape(u), r, text, flags=re.I)
    print(text)

main()
