"""Tier 1 and 2's work done once, not per case (README "What a case reuses";
backshot2.md, "Tiers"): the rules for when a case may take it.

- The prepared source (bs2-host source): ht1.sh's own source and backup
  phases, run once per source, as a case would run them (HT_VARIANT=bs2,
  BS2_PREPARE=1), kept in ROOT/sources/SOURCE/preps/NAME with prep.json:
  the overlays the source booted from (src-*.qcow2, shut down as ever), its
  dump, boot state, lvmid, health and serial log (logs/), and one backup of
  that tree (backup/repo) with its fsmeta record (src-meta.json).  A case
  takes them when source_stale() finds nothing: the same generation, the
  same bko-holt (the dump is bko-holt's), the same ht1.sh, ht1-lib.sh,
  ht1-bs2.sh, vm-run, vm-mount and fsmeta.py (what made the records), the
  same vm-run-options and image files.  Else it boots the source itself.
- The prepared backup: only for a case whose backup would be the same tree
  (backup_shareable(): no content drift, one snapshot; nothing else a case
  carries changes what is backed up), when the case's source is the
  prepared one and the rustic-bko is the one that made it (backup_stale()).
- The rescuer's saved state: rescuer_key() names one per rescuer image,
  firmware mode, memory, QEMU, OVMF, host kernel and CPU, and the scripts
  that make and resume it (vm-run, ht1-bs2.sh, bs2-qmp.py); any change is
  another key, so a stale state is never found, only rebuilt.

Pure functions here, plus the collectors that read the files (fields_now()
and rescuer_fields_now()); bs2-host's "prepared" and "rescuer-key" print
what ht1-bs2.sh reads.
"""

import hashlib
import json
import os
import platform
import shlex
import subprocess

# 2: ht1.sh's own source and backup phases (BS2_PREPARE), kept in preps/NAME
# with prep.json.  (1, unmarked: bs2-host's own boot and dump, src.json only.)
PREP_VERSION = 2
# What made a preparation's records, by path under testvm/.
DRIVERS = ("holttest1d/ht1.sh", "holttest1d/ht1-lib.sh", "backshot2/ht1-bs2.sh", "vm-run", "vm-mount", "compare/fsmeta.py")
# The rescuer's saved state: its own format, and what makes and resumes it.
RESCUER_VERSION = 1
RESCUER_DRIVERS = ("vm-run", "backshot2/ht1-bs2.sh", "backshot2/bs2-qmp.py")
# Files a preparation must hold (besides its src-I.qcow2 per disk).
PREP_FILES = ("dumps/src.json", "logs/source-bootstate.txt", "logs/source-health.txt", "logs/source-lvmid.txt")
BACKUP_FILES = ("backup/repo/config", "backup/repo-password", "src-meta.json", "bs2-snapshots.json")


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except OSError:
        return "missing"
    return h.hexdigest()


def stamp(path):
    """SIZE:MTIME_NS:INODE, or "missing": a file replaced or rewritten gets another."""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    return f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"


def vm_options(vms, source):
    """(firmware options, [disk images]) as ht1.sh reads them: vm-run-options' first
    line, its --disk X (relative to vms/SOURCE) after the guest's own image."""
    d = f"{vms}/{source}"
    words = []
    try:
        with open(f"{d}/vm-run-options") as f:
            words = f.readline().split()
    except OSError:
        pass
    fw, imgs, i = [], [f"{d}/{source}.qcow2"], 0
    while i < len(words):
        if words[i] == "--disk" and i + 1 < len(words):
            x = words[i + 1]
            imgs.append(x if x.startswith("/") else f"{d}/{x}")
            i += 2
            continue
        fw.append(words[i])
        i += 1
    return fw, imgs


def fields_now(testvm, vms, source, gen, holt_sha):
    """What a preparation is keyed to, as it is now."""
    fw, imgs = vm_options(vms, source)
    return {"version": PREP_VERSION, "source": source, "gen": gen, "holt": holt_sha,
            "drivers": {p: sha256_file(os.path.join(testvm, p)) for p in DRIVERS},
            "options": fw, "images": {p: stamp(p) for p in imgs}, "disks": len(imgs)}


def source_stale(saved, now, tier=1, prep_dir=None):
    """Why the preparation SAVED (its prep.json) can't stand in for a case's own
    source phase NOW (fields_now()): [] when it can."""
    if not saved:
        return ["no preparation"]
    why = []
    if saved.get("version") != PREP_VERSION:
        why.append(f"prepared by format {saved.get('version', 1)}, not {PREP_VERSION}")
    for k, what in (("source", "source"), ("gen", "generation"), ("holt", "bko-holt"), ("options", "vm-run-options"),
                    ("disks", "disk count")):
        if saved.get(k) != now.get(k):
            why.append(f"{what}: prepared {str(saved.get(k))[:24]}, now {str(now.get(k))[:24]}")
    for p, sha in sorted((now.get("drivers") or {}).items()):
        if (saved.get("drivers") or {}).get(p) != sha:
            why.append(f"{p} changed since the preparation")
    for p, st in sorted((now.get("images") or {}).items()):
        if (saved.get("images") or {}).get(p) != st:
            why.append(f"{os.path.basename(p)} changed since the preparation")
    # Tier 2 boots the source with Secure Boot off (ht1-bs2.sh's bs2_setup):
    # a preparation booted with it on isn't that boot.
    if tier == 2 and "--uefi" in (now.get("options") or []) and "--no-secure-boot" not in now["options"]:
        why.append("tier 2 boots this source with --no-secure-boot; the preparation booted it with Secure Boot on")
    if prep_dir:
        need = list(PREP_FILES) + [f"src-{i}.qcow2" for i in range(int(saved.get("disks") or 0))]
        why += [f"the preparation has no {f}" for f in need if not os.path.exists(os.path.join(prep_dir, f))]
    return why


def backup_shareable(case):
    """(True, "") when the case's backup is the prepared one's tree, else (False, why).
    ht1-bs2.sh's phase_backup_bs2 changes the source's tree only to plant the content
    drift (bs2-host plant, from the case's content and drifted layout) and between the
    snapshots of a --snapshot case (bs2-host change, seeded by the case); the backup's
    options are fixed (--set-atime yes, the whole mount), and nothing else of the case
    (edits, targets, load options, tier, the leftovers chain) reaches it."""
    if case.get("content"):
        return False, "the case plants its content drift"
    n = int((case.get("snapshots") or {}).get("count", 1) or 1)
    if n > 1:
        return False, f"the case backs up {n} snapshots with changes between"
    return True, ""


def backup_stale(saved, rustic_sha, prep_dir=None):
    """Why the preparation's backup can't be the case's: [] when it can."""
    b = (saved or {}).get("backup") or {}
    why = []
    if not b.get("snapshot_path"):
        why.append("the preparation made no backup")
    if b.get("rustic") != rustic_sha:
        why.append(f"rustic-bko: the backup's {str(b.get('rustic'))[:12]}, the case's {str(rustic_sha)[:12]}")
    if prep_dir:
        why += [f"the preparation has no {f}" for f in BACKUP_FILES if not os.path.exists(os.path.join(prep_dir, f))]
    return why


def shell_assign(name, value):
    return f"{name}={shlex.quote(str(value))}"


# ---- the rescuer's saved state -------------------------------------------------------

def normalize_rfw(rfw):
    """The rescuer's vm-run firmware options with --uefi-vars' file left out: each case
    has its own NVRAM file, a copy of the state's (the state is the same)."""
    out, i = [], 0
    while i < len(rfw):
        if rfw[i] == "--uefi-vars":
            out += ["--uefi-vars", "(the case's copy)"]
            i += 2
            continue
        out.append(rfw[i])
        i += 1
    return out


def rescuer_key(f):
    """The key text of a saved rescuer state from its fields F (rescuer_fields_now()):
    one line each, the same text for the same rescuer, QEMU and host."""
    lines = [f"bs2-rescuer-state {RESCUER_VERSION}",
             f"image {f['image']} {f['image_stamp']} sha256-file {f['image_sha']}",
             "rfw " + " ".join(normalize_rfw(f["rfw"])),
             f"memory {f['memory']}",
             f"qemu {f['qemu']} {f['qemu_stamp']}",
             f"host {f['kernel']} {f['cpu']}",
             f"extra {f['extra']}"]
    lines += [f"ovmf {p} {s}" for p, s in sorted((f.get("ovmf") or {}).items())]
    lines += [f"driver {p} {s}" for p, s in sorted((f.get("drivers") or {}).items())]
    return "\n".join(lines) + "\n"


def rescuer_kind(f):
    """What a state stands for (the rescuer and its firmware mode): a newer state of the
    same kind supersedes an older one."""
    return hashlib.sha256(f"{f['image']} {' '.join(normalize_rfw(f['rfw']))}".encode()).hexdigest()[:16]


def key_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _cpu():
    model = micro = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                k = k.strip()
                if k == "model name" and not model:
                    model = v.strip()
                elif k == "microcode" and not micro:
                    micro = v.strip()
                if model and micro:
                    break
    except OSError:
        pass
    return f"{model or platform.processor() or '?'} microcode {micro or '?'}"


def rescuer_fields_now(testvm, image, rfw, memory, extra):
    try:
        with open(image + ".sha256") as f:
            image_sha = (f.readline().split() or ["none"])[0]
    except OSError:
        image_sha = "none"
    try:
        qemu = subprocess.run(["qemu-system-x86_64", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    except (OSError, IndexError):
        qemu = "none"
    qbin = None
    for d in os.environ.get("PATH", "/usr/bin").split(":"):
        if os.access(os.path.join(d, "qemu-system-x86_64"), os.X_OK):
            qbin = os.path.realpath(os.path.join(d, "qemu-system-x86_64"))
            break
    ovmf = {}
    if "--uefi" in rfw:
        sb = "--no-secure-boot" not in rfw
        for n in (("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd") if sb else ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd")):
            p = f"/usr/share/OVMF/{n}"
            ovmf[p] = stamp(p)
    return {"image": os.path.realpath(image), "image_stamp": stamp(image), "image_sha": image_sha, "rfw": list(rfw),
            "memory": str(memory), "qemu": qemu, "qemu_stamp": stamp(qbin) if qbin else "missing",
            "kernel": platform.release(), "cpu": _cpu(), "extra": extra, "ovmf": ovmf,
            "drivers": {p: sha256_file(os.path.join(testvm, p)) for p in RESCUER_DRIVERS}}


def load_prep(prep_dir):
    try:
        with open(os.path.join(prep_dir, "prep.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
