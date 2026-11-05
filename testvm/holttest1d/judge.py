#!/usr/bin/env python3
"""Compare two bko-holt dumps of one machine: the source's, and the restored
copy's after it booted.  Prints one DIFF line per field that differs, and
exits 1 if any field outside IGNORED differs.

Usage: judge.py SRC.json DST.json
       judge.py fs FSMETA.py SRC-META.json DST-META.json

The fs form judges fsmeta.py's records of the source and the restored disk:
every path must match but what load's boot stage writes by design (BOOT_STAGE).

IGNORED lists what may differ between two boots of the same machine, or can't
be kept by a restore, each with why.  judge.py prints which of them did differ,
so the list can't grow without anyone seeing it.
"""

import json
import re
import sys

# Field-path patterns (dot-separated, list indexes as numbers) and why
# they may differ.
IGNORED = [
    (r"^generated$", "when the dump was made"),
    (r"^tool\.version$", "the bko-holt build"),
    (r"(^|\.)(used_bytes|available_bytes|free_blocks|free_inodes|bytes_used)$", "usage changes as the machine runs"),
    (r"(^|\.)(mkfs_time|creation_time|update_time|generation|events)$", "when a filesystem or array was made or last written"),
    (r"(^|\.)state$", "a mounted filesystem's clean flag"),
    (r"(^|\.)(last_mounted_on)$", "ext4 records where it was last mounted; the restore mounted it under /mnt/bko-target"),
    (r"^warnings$", "compared separately"),
    (r"(^|\.)(sync_action|degraded)$", "md's background state"),
    (r"^boot\.kernels\.installed\.\d+\.initrd_bytes$", "load's boot stage rebuilds every initramfs (update-initramfs -k all)"),
]

# Paths (relative to /) load's boot stage writes, and the kinds of difference
# each may show.  Anything else that differs is the restore's fault.
_WRITTEN = {"content", "mtime", "mtime (dirs)", "atime", "atime (dirs)", "extra"}
BOOT_STAGE = [
    (r"^boot/grub(/|$)", _WRITTEN | {"missing"}, "grub-install and update-grub rewrite /boot/grub"),
    (r"^boot/initrd\.img-[^/]+$", _WRITTEN, "update-initramfs -k all"),
    (r"^boot$", {"mtime (dirs)", "atime (dirs)"}, "update-initramfs replaces the initrds"),
    (r"^var/log/bko-holt(/|$)", _WRITTEN, "load's own log, copied in by the boot stage"),
    (r"^var/log$", {"mtime (dirs)", "atime (dirs)"}, "var/log/bko-holt made"),
    (r"^var/lib/initramfs-tools(/|$)", _WRITTEN, "update-initramfs records the kernels it built for"),
    (r"^(tmp|var/tmp)$", {"mtime (dirs)"}, "update-initramfs and update-grub work in temporary directories"),
    (r"^var/lib/os-prober(/|$)", _WRITTEN, "update-grub runs os-prober (GRUB_DISABLE_OS_PROBER=false)"),
]


def flatten(v, path, out):
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{path}.{k}" if path else k, out)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            flatten(x, f"{path}.{i}" if path else str(i), out)
    else:
        out[path] = v


def judge_fs(fsmeta_py, src_path, dst_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fsmeta", fsmeta_py)
    fsmeta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsmeta)
    diff = fsmeta.differences(json.load(open(src_path)), json.load(open(dst_path)))
    real, allowed = {}, {}
    for kind, paths in diff.items():
        for p in paths:
            why = next((w for r, kinds, w in BOOT_STAGE if kind in kinds and re.search(r, p)), None)
            (allowed.setdefault(why, []) if why else real.setdefault(kind, [])).append(p)
    for kind, paths in sorted(real.items()):
        print(f"DIFF {kind}: {len(paths)}: {', '.join(paths[:5])}{' ...' if len(paths) > 5 else ''}")
    for why, paths in allowed.items():
        print(f"boot stage ({why}): {len(paths)}: {', '.join(paths[:4])}{' ...' if len(paths) > 4 else ''}")
    n = sum(len(v) for v in real.values())
    print(f"{n} differences, {sum(len(v) for v in allowed.values())} from the boot stage")
    sys.exit(1 if n else 0)


def main():
    if sys.argv[1] == "fs":
        judge_fs(*sys.argv[2:5])
    src = json.load(open(sys.argv[1]))
    dst = json.load(open(sys.argv[2]))
    a, b = {}, {}
    flatten(src, "", a)
    flatten(dst, "", b)
    real, ignored = [], {}
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<absent>") == b.get(k, "<absent>"):
            continue
        why = next((w for p, w in IGNORED if re.search(p, k)), None)
        if why:
            ignored.setdefault(why, []).append(k)
        else:
            real.append(k)
            print(f"DIFF {k}: {json.dumps(a.get(k, '<absent>'))[:200]} -> {json.dumps(b.get(k, '<absent>'))[:200]}")
    for why, keys in ignored.items():
        print(f"ignored ({why}): {len(keys)}: {', '.join(keys[:6])}{' ...' if len(keys) > 6 else ''}")
    if dst.get("warnings"):
        print(f"restored dump's warnings: {dst['warnings']}")
    print(f"{len(real)} differences, {sum(len(v) for v in ignored.values())} ignored, of {len(set(a) | set(b))} fields")
    sys.exit(1 if real else 0)


if __name__ == "__main__":
    main()
