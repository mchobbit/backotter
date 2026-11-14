#!/usr/bin/env python3
"""Back up a test guest with several archivers, restore each backup onto
blank disks, and check what survived.

Usage: sudo compare.py [--mkfs-from STICK_IMG] GUEST [TOOL ...]
                                    tools: restic borg dar bsdtar (default), restic-bko,
                                    rustic-bko, restic-to-rustic and rustic-to-restic
       sudo compare.py --boot-only [--mkfs-from STICK_IMG] GUEST [TOOL ...]
                                    boot an earlier run's restores again

restic-bko is BackOtter's patched restic (branch "backotter" of
github.com/mchobbit/restic): $RESTIC_BKO, or else the newest Linux build in
~/backotter-work/restic/dist/.  rustic-bko is BackOtter's rustic (branch
"backotter" of github.com/mchobbit/rustic and rustic_core): $RUSTIC_BKO, or else
the newest Linux build in ~/backotter-work/rustic/dist/.  restic-to-rustic
backs up with restic-bko and restores with rustic-bko, rustic-to-restic the
other way round: the two read and write the same metadata.

For GUEST (a folder in ~/backotter-work/vms holding GUEST.qcow2 at its
fresh-install state, and any other disks its vm-run-options name with
--disk), in ~/backotter-work/compare/GUEST/:
  1. src.qcow2 (and src-1.qcow2... for the other disks), throwaway overlays
     on the guest's disks, get torture.sh's hard-to-preserve files; the
     guest's own images are never written.  Where this host can't mount the
     guest's file systems read-write (a ZFS pool with features this host's
     OpenZFS lacks, which it can import read-only only), the guest makes
     them itself: booted from src, with acl and attr installed where it has
     apt and lacks them.
  2. src, so changed, is booted (with its writes in RAM): what its init says
     of its services is what a restore's boot is held against.
  3. Each tool backs up / and everything mounted below it (e.g. /boot), from
     src mounted read-only, as a backup taken from a rescue system would.
     A mounted btrfs subvolume that shows up again nested in another one
     mounted (openSUSE's /, again at /.snapshots/1/snapshot) is backed up
     once, where it is mounted.
  4. Each backup is restored onto blank disks laid out like the original's:
     the same partition tables and boot code (the first MiB, which holds
     GRUB's core image on the BIOS + MBR guests, and a BIOS boot partition,
     which holds it on BIOS + GPT ones: rocky10-lvm), mdadm arrays, LUKS2
     volumes, LVM volume groups (their metadata restored as it was: linear
     and striped LVs) and filesystems recreated with the same UUIDs and
     labels, by
     the guest's own mkfs tools (so the guest's GRUB and kernel can read
     them), btrfs subvolumes, ZFS pools (mirrors too, with the original's
     feature flags as far as this host's OpenZFS has them, and its
     encryption, with the same key) and bcachefs's on-disk version included.
     A LUKS volume gets the passphrase in vms/GUEST/luks-passphrase,
     console-passphrase or zfs-passphrase; a ZFS volume (zvol, such as
     Ubuntu's LUKS keystore) is copied as it is, since the archivers back
     up files only.
  5. fsmeta.py compares every file's metadata before and after, and the
     restored disks are booted to see whether they come up as the original
     did: boot-state.sh reports what the guest's init (systemd, OpenRC,
     runit, dinit, s6-rc or sysv-rc) says of its services, and what failed
     in a restore's boot but not in the original's is that restore's.
     Between the two, a ZFS root's restore gets what any restore of one
     must do, after the comparison so that this stays the archiver's
     alone: the new pool's cache file in /etc/zfs/zpool.cache, and the
     initramfs rebuilt with it (retarget_zfs_boot()).
That is the archiver's part of a bare-metal restore; recreating a layout
from a recorded description, as BackOtter must, is not tested here.

A boot types the guest's disk passphrase where it asks for one: on the
serial console, with vms/GUEST/zfs-passphrase (Ubuntu's ZFS keystore) or
console-passphrase (a LUKS root's initramfs: debian13-stack), or on its
keyboard (Omarchy's Plymouth), with vms/GUEST/luks-passphrase.

--mkfs-from STICK_IMG makes the filesystems with the mkfs tools of BackOtter's
rescue stick (STICK_IMG, from stick/make-img.sh) instead of the guest's own,
as a restore from the stick would: newer tools turn on features an older
guest's kernel or GRUB may not read.  ZFS pools are still made by the host's
zpool, with the original's feature flags, as BackOtter must make them.  The
results go to ~/backotter-work/compare/GUEST.stick-mkfs/.  A guest with
bcachefs needs it: the kernel mounts no two bcachefs file systems with one
UUID, so src is unmounted (after the backups) before the restores, and the
guest's own mkfs can't be run from it.

The restores boot with the vm-run options the guest's builder left in
vms/GUEST/vm-run-options (e.g. --uefi, with Secure Boot), its --disk ones
replaced by the restored disks; without that file, by BIOS, or by UEFI
without Secure Boot if the guest has an EFI system partition.  Each boot
gets a fresh UEFI variable store, as a new machine's.

COMPARE_SLOT=N (0-9, default 0) gives a run its own mount points, pool
name and SSH port (2230 + N), so that runs on different guests can go at
once on one host.  Runs of several sessions on one host, even of the same
guest, keep apart with:
  COMPARE_NAME=NAME  (a letter, then up to 15 letters, digits or _) the
                     prefix of everything the run names: mount points
                     /mnt/NAME-src, -dst, -stick, -stick-parts, -btrfs-top
                     and -dst-keystore, the restored pool's temporary name
                     and its md arrays and LUKS volumes (NAME-dst...), and
                     the work directory, ~/backotter-work/compare/NAME/GUEST/
                     (instead of COMPARE_SLOT's names; its mount points are
                     removed again at the end)
  COMPARE_WORK=DIR   the work directories in DIR/GUEST/ instead
  COMPARE_PORT=PORT  the boots' SSH port instead of 2230 + COMPARE_SLOT
  COMPARE_NBD=LIST   the nbd devices it (and vm-mount for it) may take, e.g.
                     "40-47" or "nbd40 nbd41,nbd50" (default: any free one,
                     chosen when attaching, under the host's nbd lock)
Two runs of the same guest at once still give the host two file systems
with one UUID: btrfs and bcachefs may refuse to mount the second.
COMPARE_GROW_GIB=N makes each restored disk N GiB
bigger than the original, its last partition grown to the end, for a
guest whose files take more room restored one by one than they did
(Omarchy's: btrfs snapshots sharing their data, compressed).
Where /mnt/bko-ram (a tmpfs of its own) is mounted, a run works in RAM,
in /mnt/bko-ram/compare-NAME/GUEST/, sparing the host's disk, and copies
its results, diffs and logs to the work directory above when it ends,
however it ends; COMPARE_WORK, or COMPARE_RAM=0, keeps it on disk.
COMPARE_DISCARD=1 (the default in RAM) deletes each repository once
restored from and each restored disk once booted: one direction at a
time, at most one repository and one restored disk, where otherwise every
restored disk stays (to look at, or boot again with --boot-only, which
needs a run on disk).
COMPARE_DISKS_DIR=DIR puts the restored disks in DIR/compare-NAME/GUEST/
instead, for a guest too big to restore in RAM: its repositories stay in
RAM, its restored disks go to a disk that runs cool (/bkoscratch), and a
run in RAM deletes them with its RAM directory.

Results: results.json and a table on stdout.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import signal
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK_BASE = HERE.parents[1]                       # ~/backotter-work
TESTVM = HERE.parent
VMS = WORK_BASE / "vms"
SLOT = os.environ.get("COMPARE_SLOT", "0")
if not re.fullmatch(r"[0-9]", SLOT):
    raise SystemExit("COMPARE_SLOT must be a digit")
NAME = os.environ.get("COMPARE_NAME", "")
if NAME and (not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", NAME)
             or re.match(r"(mirror|raidz|draid|spare|c[0-9])", NAME)):
    # The restored pool is named NAME-dst: no vdev keyword, no Solaris disk name.
    raise SystemExit("COMPARE_NAME: a letter, then up to 15 letters, digits or _, "
                     "not starting with a ZFS vdev keyword or c+digit")
PREFIX = NAME or "cmp"
SUFFIX = "" if NAME or SLOT == "0" else f"-{SLOT}"
SRC_MP = Path(f"/mnt/{PREFIX}-src{SUFFIX}")
DST_MP = Path(f"/mnt/{PREFIX}-dst{SUFFIX}")
STICK_MP = Path(f"/mnt/{PREFIX}-stick{SUFFIX}")   # --mkfs-from: the stick's root
SSH_PORT = os.environ.get("COMPARE_PORT") or str(2230 + int(SLOT))
if not re.fullmatch(r"[0-9]{4,5}", SSH_PORT) or not 1024 <= int(SSH_PORT) <= 65535:
    raise SystemExit("COMPARE_PORT must be a port number from 1024 to 65535")
PASSWORD = "backotter-compare"                    # for restic's and borg's encryption
DST_POOL_TEMP = f"{PREFIX}-dst{SUFFIX}"
DST_NAME = f"{PREFIX}-dst{SUFFIX}"                # the restored disks' LUKS, md and VG names start so
BIOS_BOOT = "21686148-6449-6e6f-744e-656564454649"   # GPT's BIOS boot partition type
# LVM sees only the devices each command names (--devices), never by this
# host's devices file: src's and dst's groups have the same UUIDs.
LVM_OPTS = ["--devicesfile", ""]
# The work directories' parent: COMPARE_WORK, else compare/ (compare/NAME/
# with COMPARE_NAME).
COMPARE_DIR = (Path(os.environ["COMPARE_WORK"]).resolve() if os.environ.get("COMPARE_WORK")
               else WORK_BASE / "compare" / NAME if NAME else WORK_BASE / "compare")
# A run writes gigabytes (repositories, restored disks, overlays), which the
# hosts' disks shouldn't take (Chopin's overheated): where /mnt/bko-ram, a
# tmpfs of its own, is mounted, a run works there, and its results, diffs
# and logs go to COMPARE_DIR at the end.  Not with COMPARE_WORK, nor with
# COMPARE_RAM=0.
RAM_BASE = Path("/mnt/bko-ram")
IN_RAM = (not os.environ.get("COMPARE_WORK") and os.environ.get("COMPARE_RAM", "1") != "0"
          and os.path.ismount(RAM_BASE))
RUN_DIR = RAM_BASE / f"compare-{PREFIX}{SUFFIX}" if IN_RAM else COMPARE_DIR


def nbd_allowed(spec: str) -> list[str] | None:
    """COMPARE_NBD's devices, as names (nbd40...), or None for any."""
    if not spec.strip():
        return None
    names: list[str] = []
    for item in re.split(r"[\s,]+", spec.strip()):
        m = re.fullmatch(r"(?:/dev/)?(?:nbd)?(\d+)(?:-(?:/dev/)?(?:nbd)?(\d+))?", item)
        if not m:
            raise SystemExit(f"COMPARE_NBD: can't read {item!r} (e.g. \"40-47\" or \"nbd40 nbd41\")")
        lo, hi = int(m[1]), int(m[2] if m[2] is not None else m[1])
        names += [f"nbd{i}" for i in range(lo, hi + 1)]
    return names


NBD_ALLOWED = nbd_allowed(os.environ.get("COMPARE_NBD", ""))
if NBD_ALLOWED is not None:
    # vm-mount, run from here, takes only these too.
    os.environ["VM_MOUNT_NBD"] = " ".join(NBD_ALLOWED)
BOOT_STATE = HERE / "boot-state.sh"
GROW_GIB = int(os.environ.get("COMPARE_GROW_GIB", "0"))
DISCARD = os.environ.get("COMPARE_DISCARD", "1" if IN_RAM else "") == "1"
DISKS_DIR = (Path(os.environ["COMPARE_DISKS_DIR"]).resolve() / f"compare-{PREFIX}{SUFFIX}"
             if os.environ.get("COMPARE_DISKS_DIR") else None)


def run(cmd: list[str] | str, check: bool = True, quiet: bool = False, **kw) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = ["bash", "-c", cmd]
    if not quiet:
        print("  $", " ".join(shlex.quote(c) for c in cmd), flush=True)
    # errors="replace": torture.sh's file names aren't all UTF-8, and bsdtar
    # quotes them as they are in its warnings.
    r = subprocess.run(cmd, text=True, errors="replace", capture_output=True, **kw)
    if check and r.returncode != 0:
        print(f"  failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-2000:]}", flush=True)
        r.check_returncode()
    return r


def out(cmd: list[str] | str, **kw) -> str:
    return run(cmd, quiet=True, **kw).stdout.strip()


# --- The guest ----------------------------------------------------------------------

class GuestConfig:
    """What vms/GUEST says of how to boot the guest: vm-run's options for its
    hardware (vm-run-options without its --disk ones, or None without that
    file), its other disks (those --disk ones), and the file holding its
    disks' passphrase, with how it is asked for ("luks": on the keyboard,
    "zfs" or "console": on the serial console)."""

    def __init__(self, guest_dir: Path):
        self.dir = guest_dir
        self.base = guest_dir / f"{guest_dir.name}.qcow2"
        # A guest whose sshd serves no logins (Redox today): compare runs
        # everything in it over SSH.
        no_ssh = guest_dir / "no-ssh"
        if no_ssh.exists():
            raise SystemExit(f"compare.py: {guest_dir.name} has no SSH, which compare needs: "
                             f"{no_ssh.read_text().strip()}")
        f = guest_dir / "vm-run-options"
        opts = shlex.split(f.read_text()) if f.exists() else None
        self.options: list[str] | None = None if opts is None else []
        self.disks: list[Path] = []
        while opts:
            o = opts.pop(0)
            if o == "--disk" and opts:
                self.disks.append(guest_dir / opts.pop(0))
            else:
                self.options.append(o)
        self.key = self.key_kind = None
        for kind in ("luks", "zfs", "console"):
            if (guest_dir / f"{kind}-passphrase").exists():
                self.key, self.key_kind = guest_dir / f"{kind}-passphrase", kind
                break

    def passphrase(self) -> bytes:
        """The passphrase without the file's final newline, as typed at boot."""
        return self.key.read_bytes().rstrip(b"\n")

    def hardware(self, layout: dict) -> list[str]:
        """vm-run's options for the guest's hardware: vm-run-options' own, or
        else UEFI without Secure Boot (which Pop!_OS doesn't support) if it
        has an EFI system partition (FAT), BIOS if not."""
        if self.options is not None:
            return self.options
        return ["--uefi", "--no-secure-boot"] if "vfat" in layout.values() else []


def disk_images(stem: Path, n: int) -> list[Path]:
    """STEM.qcow2 and the n-1 more disks beside it, STEM-1.qcow2..."""
    return [stem.with_name(stem.name + (f"-{i}" if i else "") + ".qcow2") for i in range(n)]


# --- Disks ---------------------------------------------------------------------------

def free_nbd() -> str:
    for b in sorted(Path("/sys/block").glob("nbd*"), key=lambda p: int(p.name[3:])):
        if NBD_ALLOWED is not None and b.name not in NBD_ALLOWED:
            continue
        if not (b / "pid").exists():
            return f"/dev/{b.name}"
    raise SystemExit("no free nbd device" + (" in COMPARE_NBD" if NBD_ALLOWED is not None else ""))


def attach(image: Path) -> str:
    # A disk off tmpfs (a "!" guest's, in COMPARE_DISKS_DIR) is served through
    # that filesystem's page cache, so its qemu-nbd gets a scope of its own.
    # In cmp-run.sh's MemoryHigh scope, with the guest filesystem whose
    # writeback it serves, it is throttled at the cap (memory.high reclaim,
    # the scope's dirty limit) on pages only it can clean, and the restore
    # stops: nbd stuck, dm-crypt in wbt_wait (omarchy, 2026-11-04). PartOf:
    # it still goes when this run's scope is stopped, but not when the scope
    # just empties (BindsTo would): a pool left imported when its export
    # failed must keep its disks. Only a system scope: PID 1 has no user
    # manager's (tmux panes). On tmpfs it stays: its pages are RAM-disk room,
    # and the cap must see them.
    wrap = []
    if out(["stat", "-f", "-c", "%T", str(image)]) != "tmpfs":
        cg = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[-1]
        wrap = ["systemd-run", "--quiet", "--scope", "--collect", "--property=IOAccounting=yes",
                f"--unit=bko-nbd-{os.getpid()}-{time.monotonic_ns()}"]
        if re.fullmatch(r"/system\.slice/[^/]+\.scope", cg):
            wrap.append(f"--property=PartOf={cg.rsplit('/', 1)[-1]}")
    # The same lock vm-mount and make-from-live.sh take: finding a free device
    # and attaching it must be one step.
    import fcntl
    with open("/run/lock/backotter-nbd.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        dev = free_nbd()
        run(wrap + ["qemu-nbd", "--connect", dev, "--format", "qcow2", str(image)])
    run(["udevadm", "settle"], quiet=True)
    for _ in range(10):
        if Path(f"{dev}p1").exists():
            break
        time.sleep(0.5)
    return dev


def detach(dev: str) -> None:
    # Disconnecting returns before the qemu-nbd server has exited; the next
    # user of the device would get its late replies, or find the image still
    # locked, so wait for it.  The device's pid is the thread serving it,
    # which ends first: wait for the process, its thread group.
    pid_file = Path("/sys/block") / Path(dev).name / "pid"
    pid = None
    try:
        tid = pid_file.read_text().strip()
        pid = int(next(l.split()[1] for l in Path(f"/proc/{tid}/status").read_text().splitlines()
                       if l.startswith("Tgid:")))
    except (OSError, StopIteration, ValueError):
        pass
    # The wear log: what a qemu-nbd scope of its own (attach()) wrote to disk,
    # read before it goes, as its job's (iolog.sh syncs first).
    if pid is not None:
        try:
            cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[-1]
            mine = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[-1]
            if re.fullmatch(r"/system\.slice/bko-nbd-[^/]+\.scope", cg):
                run([str(Path(__file__).resolve().parent / "iolog.sh"), "--cgroup", f"/sys/fs/cgroup{cg}",
                     f"nbd for {mine.rsplit('/', 1)[-1]}"], check=False, quiet=True)
        except OSError:
            pass
    run(["qemu-nbd", "--disconnect", dev], check=False, quiet=True)
    while pid is not None and Path(f"/proc/{pid}").exists():
        time.sleep(0.2)


def vm_mount(images: list[Path], mp: Path, ro: bool, key: Path | None) -> list[str]:
    """vm-mount the guest's disks at mp; returns vm-mount's record of what it
    set up: the first disk's nbd device, then lines such as "nbd DEVICE"
    (another disk), "md DEVICE", "loop DEVICE" (a RAID 1 read through one
    member, with --ro), "luks NAME" and "pool NAME"."""
    run([str(TESTVM / "vm-mount")] + (["--ro"] if ro else [])
        + [a for d in images[1:] for a in ("--disk", str(d))]
        + (["--key", str(key)] if key else []) + [str(images[0]), str(mp)])
    state = Path("/run/vm-mount") / out(["systemd-escape", "--path", str(mp)])
    return [line for line in state.read_text().splitlines() if line.strip()]


def vm_umount(mp: Path) -> None:
    run([str(TESTVM / "vm-umount"), str(mp)], check=False)


def blkid(dev: str) -> dict[str, str]:
    r = run(["blkid", "-o", "export", dev], check=False, quiet=True)
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def bcachefs_version(dev: str) -> str:
    """The on-disk format version in dev's bcachefs superblock, as "MAJOR.MINOR"."""
    with open(dev, "rb") as f:
        f.seek(8 * 512 + 16)        # struct bch_sb, at sector 8: le16 version after the checksum
        v = int.from_bytes(f.read(2), "little")
    return f"{v >> 10}.{v & 0x3ff}"


def bcachefs_block_size(dev: str) -> int:
    """The block size in dev's bcachefs superblock, in bytes."""
    with open(dev, "rb") as f:
        f.seek(8 * 512 + 120)       # struct bch_sb: le16 block_size, in sectors, after offset and seq
        return int.from_bytes(f.read(2), "little") * 512


def mounts_below(mp: Path) -> list[dict[str, str]]:
    """findmnt's view of mp and everything mounted below it, parents first."""
    rows = json.loads(out(["findmnt", "-J", "-R", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS", str(mp)]))
    flat: list[dict[str, str]] = []

    def walk(nodes):
        for n in nodes:
            # findmnt -J names its fields in lower case.
            flat.append({k.upper(): v for k, v in n.items() if k != "children"})
            walk(n.get("children", []))
    walk(rows["filesystems"])
    return flat


def remount_noatime(mp: Path) -> None:
    """Remount mp and everything below it noatime: fsmeta.py reads the
    restored tree, which would change the atimes it records."""
    for m in mounts_below(mp):
        run(["mount", "-o", "remount,noatime", m["TARGET"]], check=False)


def partitions(disk: str) -> list[str]:
    """disk's partitions, in order."""
    return [str(p) for p in sorted(Path("/dev").glob(Path(disk).name + "p*"),
                                   key=lambda p: int(p.name.rsplit("p", 1)[1]))]


def guest_path(root: Path, path: str) -> Path | None:
    """path inside the guest mounted at root, its symlinks followed as the
    guest would follow them (an absolute link means root/...): NixOS's
    /etc/fstab, for one, is a link into /nix/store.  None if there is no
    such file."""
    parts, done = path.strip("/").split("/"), []
    for _ in range(100):
        if not parts:
            p = root.joinpath(*done)
            return p if p.is_file() else None
        name = parts.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            done = done[:-1]
            continue
        host = root.joinpath(*done, name)
        if host.is_symlink():
            target = os.readlink(host)
            if target.startswith("/"):
                done = []
            parts = target.strip("/").split("/") + parts
            continue
        done.append(name)
    return None


def tools_root(root: Path) -> Path:
    """The system mounted at root, as a root to chroot into: root itself, or
    on ostree, where vm-mount mounted the physical root (ostree/repo and the
    deployments, but no /etc, /usr or /bin) at root, its default deployment
    (testvm/ostree_paths.py), whose /etc is the system's."""
    if (root / "etc").exists() or not (root / "ostree" / "deploy").is_dir():
        return root
    if str(TESTVM) not in sys.path:
        sys.path.insert(0, str(TESTVM))
    import ostree_paths
    d = ostree_paths.default_deployment(str(root))
    if d is None:
        raise SystemExit(f"{root} holds ostree deployments, but no default one")
    return root / d


def bind_point(root: Path) -> str:
    """A directory in the system at root to bind something at for a command
    run there: /mnt, or where /mnt is a link (ostree's, to var/mnt, empty
    in a deployment) /sysroot, which a deployment has empty."""
    for d in ("mnt", "sysroot"):
        if (root / d).is_dir() and not (root / d).is_symlink():
            return d
    raise SystemExit(f"no /mnt or /sysroot directory in {root}")


def fstab_options(root: Path) -> dict[str, list[str]]:
    """The guest's fstab: each mount point's options."""
    f = guest_path(tools_root(root), "/etc/fstab")
    opts = {}
    for line in f.read_text().splitlines() if f else []:
        fields = line.split()
        if len(fields) >= 4 and not fields[0].startswith("#"):
            opts[fields[1]] = fields[3].split(",")
    return opts


def wait_for(path: str) -> None:
    run(["udevadm", "settle"], quiet=True)
    for _ in range(20):
        if Path(path).exists():
            return
        time.sleep(0.5)
    raise SystemExit(f"{path} never appeared")


# --- Recreating the layout ----------------------------------------------------------

class Layout:
    """What src looks like: its disks' partitions, the mdadm arrays and LUKS
    volumes on them, what each holds, and how it is all mounted.  Block
    devices go by src's device names, as vm-mount set them up."""

    def __init__(self, state: list[str], key: Path | None, work: Path):
        self.disks = [state[0]] + [line.split()[1] for line in state[1:] if line.startswith("nbd ")]
        self.fields: dict[str, dict[str, str]] = {}      # every block device -> blkid's fields
        self.parts: dict[str, int] = {}                  # partition -> its disk's index
        for i, disk in enumerate(self.disks):
            for p in partitions(disk):
                self.parts[p] = i
                self.fields[p] = blkid(p)
        # mdadm arrays: the device vm-mount reads each through (md, or with
        # --ro a loop device over one member) -> its members, in role order,
        # and their superblock's version, level, UUID and name.
        self.md: dict[str, dict] = {}
        # LUKS volumes: the opened device -> the device it is on, and what
        # luksDump says of it.
        self.luks: dict[str, dict] = {}
        # LVM volume groups: name -> their PVs (src's devices) and each's
        # UUID, the metadata backup (vgcfgbackup) and each LV's device.
        self.lvm: dict[str, dict] = {}
        for line in state[1:]:
            kind, _, what = line.partition(" ")
            if kind in ("md", "loop"):
                dev = os.path.realpath(what)
                if kind == "md":
                    members = [f"/dev/{s.name}" for s in Path(f"/sys/block/{Path(dev).name}/slaves").iterdir()]
                else:
                    back = out(["losetup", "-n", "-O", "BACK-FILE", dev])
                    uuid = blkid(back).get("UUID")
                    members = [p for p in self.parts if self.fields[p].get("TYPE") == "linux_raid_member"
                               and self.fields[p].get("UUID") == uuid]
                self.md[dev] = self.md_layout(members)
                self.fields[dev] = blkid(dev)
            elif kind == "luks":
                # By its dm-N name, as findmnt's /dev/mapper/NAME resolves.
                dev = os.path.realpath(f"/dev/mapper/{what}")
                on = os.path.realpath("/dev/" + what.split("-", 3)[3])     # vm-mount-PID-DEVICE
                dump = json.loads(out(["cryptsetup", "luksDump", "--dump-json-metadata", on]))
                self.luks[dev] = {"on": on, "dump": dump, "uuid": blkid(on).get("UUID"),
                                  "label": blkid(on).get("LABEL")}
                self.fields[dev] = blkid(dev)
            elif kind in ("vg", "lvm"):
                # "lvm": vm-mount set the LVs up by hand, under names of its
                # own ("lv VG LV DMNAME" lines); "vg": LVM activated them.
                vg, pvlist = what.split()
                names = {l.split()[2]: l.split()[3] for l in state[1:]
                         if kind == "lvm" and l.startswith("lv ") and l.split()[1] == vg}
                self.lvm[vg] = self.lvm_layout(vg, pvlist, work, names)
                for dev in self.lvm[vg]["lvs"].values():
                    self.fields[dev] = blkid(dev)
        # What vm-mount left closed (a RAID it can't read with --ro, LUKS
        # without a key) would come back as blank partitions.
        in_md = {m for md in self.md.values() for m in md["members"]}
        for p, f in list(self.fields.items()):
            if f.get("TYPE") == "linux_raid_member" and p not in in_md:
                raise SystemExit(f"{p} is in a RAID that vm-mount didn't set up")
            if f.get("TYPE") == "crypto_LUKS" and p not in {l["on"] for l in self.luks.values()}:
                raise SystemExit(f"{p} is LUKS-encrypted, and vm-mount didn't open it (no passphrase file?)")
            if f.get("TYPE") == "LVM2_member" and p not in {pv for v in self.lvm.values() for pv in v["pvs"]}:
                raise SystemExit(f"{p} is an LVM PV in no volume group vm-mount activated")
        for dev, f in self.fields.items():
            if f.get("TYPE") == "bcachefs":
                f["BCACHEFS_VERSION"] = bcachefs_version(dev)
                f["BCACHEFS_BLOCK_SIZE"] = str(bcachefs_block_size(dev))
        self.mounts = mounts_below(SRC_MP)
        self.aliases = self.btrfs_aliases()
        self.fstab = fstab_options(SRC_MP)
        self.zfs = None
        pool = next((line.split()[1] for line in state[1:] if line.startswith("pool ")), None)
        if pool:
            self.zfs = self.zfs_layout(pool, key, work)

    @staticmethod
    def lvm_layout(vg: str, pvlist: str, work: Path, names: dict[str, str] | None = None) -> dict:
        """Volume group vg, on src's devices pvlist (vm-mount's record): its
        PVs (by src's device names) with their UUIDs, the metadata
        (vgcfgbackup: every UUID, extent and segment) and each logical
        volume's device: /dev/mapper/NAME for NAMES (LV -> the dm name
        vm-mount gave it), else the one LVM activated."""
        opts = LVM_OPTS + ["--devices", pvlist]
        backup = work / f"lvm-{vg}.vg"
        run(["vgcfgbackup"] + opts + ["-f", str(backup), vg], quiet=True)
        pvs = {}
        for line in out(["pvs"] + opts + ["--noheadings", "--separator", "|", "-o", "pv_name,pv_uuid,vg_name"]).splitlines():
            name, uuid, in_vg = (x.strip() for x in line.split("|"))
            if in_vg == vg:
                pvs[os.path.realpath(name)] = uuid
        lvs = {}
        for line in out(["lvs"] + opts + ["--noheadings", "--separator", "|", "-o", "lv_name,lv_dm_path,segtype",
                                          vg]).splitlines():
            name, path, segtype = (x.strip() for x in line.split("|"))
            if segtype not in ("linear", "striped"):
                raise SystemExit(f"{vg}/{name} is a {segtype} LV; recreate() makes linear and striped LVs only")
            lvs[name] = os.path.realpath(f"/dev/mapper/{names[name]}" if names else path)
        return {"pvs": pvs, "backup": backup, "lvs": lvs}

    @staticmethod
    def md_layout(members: list[str]) -> dict:
        def examine(m: str) -> dict[str, str]:
            return dict(line.strip().split(" : ", 1) for line in out(["mdadm", "--examine", m]).splitlines()
                        if " : " in line)
        info = examine(members[0])
        if not info.get("Version", "").startswith("1.") or "Array UUID" not in info:
            raise SystemExit(f"{members[0]}: recreate() makes md arrays with 1.x superblocks only")
        # "Active device 1": the member's place in the array; spares aren't
        # made, and a missing member stays missing.
        role = {}
        for m in members:
            r = re.fullmatch(r"Active device (\d+)", examine(m).get("Device Role", ""))
            if r:
                role[int(r[1])] = m
        n = int(info["Raid Devices"])
        return {"members": [role.get(i, "missing") for i in range(n)], "version": info["Version"],
                "level": info["Raid Level"], "devices": info["Raid Devices"],
                "uuid": info["Array UUID"], "name": info.get("Name", "").split(" ")[0]}

    def zfs_layout(self, temp: str, key: Path | None, work: Path) -> dict:
        label = out(["zdb", "-l", next(p for p, f in self.fields.items() if f.get("TYPE") == "zfs_member")])
        name = next(line.split("'")[1] for line in label.splitlines() if line.strip().startswith("name:"))
        features = [line.split("\t")[0] for line in out(["zpool", "get", "-H", "-o", "property,value", "all", temp]).splitlines()
                    if line.startswith("feature@") and line.split("\t")[1] in ("enabled", "active")]
        pool_props = {}
        for line in out(["zpool", "get", "-H", "-o", "property,value,source", "ashift,autotrim,compatibility", temp]).splitlines():
            prop, value, source = line.split("\t")
            if source == "local" or prop == "ashift":
                pool_props[prop] = value
        # Its top-level vdevs, from zpool status -P: a device, or "mirror-0"
        # (raidz1-0...) and the devices indented below it.
        vdevs: list[list[str]] = []
        config = out(["zpool", "status", "-P", temp]).split("config:", 1)[1].splitlines()
        depth = None
        for line in config:
            if not line.strip() or line.strip().startswith(("NAME", "errors:")):
                continue
            indent, word = len(line) - len(line.lstrip()), line.split()[0]
            if word == temp:
                depth = indent
            elif depth is not None and word in ("logs", "cache", "spares", "special", "dedup"):
                raise SystemExit(f"{temp} has {word} vdevs, which recreate() doesn't make")
            elif depth is not None and indent == depth + 2:
                vdevs.append([re.sub(r"-[0-9]+$", "", word)] if not word.startswith("/") else [os.path.realpath(word)])
            elif depth is not None and indent > depth + 2:
                vdevs[-1].append(os.path.realpath(word))
        datasets = out(["zfs", "list", "-H", "-o", "name", "-t", "filesystem,volume", "-r", temp]).splitlines()
        props: dict[str, dict[str, str]] = {d: {} for d in datasets}
        # -r: without it, zfs get reports only the pool's top dataset.
        for line in out(["zfs", "get", "-r", "-t", "filesystem,volume", "-H", "-o", "name,property,value", "-s", "local",
                         "all", temp]).splitlines():
            ds, prop, value = line.split("\t")
            if prop == "mountpoint" and value.startswith(str(SRC_MP)):
                value = "/" + value[len(str(SRC_MP)):].lstrip("/")   # undo the altroot
            if prop not in ("keylocation", "volsize"):              # made below
                props[ds][prop] = value
        info: dict[str, dict[str, str]] = {d: {} for d in datasets}
        # Those set at creation only (normalization...) are no "local" ones.
        for line in out(["zfs", "get", "-r", "-t", "filesystem,volume", "-H", "-p", "-o", "name,property,value",
                         "type,volsize,volblocksize,encryption,encryptionroot,keyformat,keylocation,pbkdf2iters,"
                         "canmount,mountpoint,normalization,utf8only,casesensitivity", temp]).splitlines():
            ds, prop, value = line.split("\t")
            if ds in info:
                info[ds][prop] = value
        z = {"name": name, "temp": temp, "features": features, "pool_props": pool_props, "vdevs": vdevs,
             "datasets": datasets, "props": props, "info": info, "key": None}
        if any(i["encryption"] != "off" for i in info.values()):
            z["key"] = self.zfs_key(temp, info, key, work)
        return z

    @staticmethod
    def zfs_key(temp: str, info: dict, key: Path | None, work: Path) -> Path:
        """A file holding the key of the pool's encrypted datasets: the key
        file inside Ubuntu's LUKS keystore volume, POOL/keystore, if there is
        one (as vm-mount loads it), else the passphrase."""
        formats = {i["keyformat"] for i in info.values() if i["encryption"] != "off"}
        if not key:
            raise SystemExit(f"{temp} is encrypted: the guest needs a zfs-passphrase file")
        if f"{temp}/keystore" not in info:
            if formats != {"passphrase"}:
                raise SystemExit(f"{temp}'s keys are {formats}, with no keystore volume to read them from")
            return key
        dst = work / "zfs-key"
        ks, name, mp = f"/dev/zvol/{temp}/keystore", f"{DST_NAME}-src-keystore", Path(f"/mnt/{DST_NAME}-keystore")
        wait_for(ks)
        run(["cryptsetup", "open", "--readonly", "--key-file", "-", ks, name], input=key.read_text().rstrip("\n"))
        try:
            mp.mkdir(exist_ok=True)
            run(["mount", "-o", "ro", f"/dev/mapper/{name}", str(mp)])
            try:
                keyfile = next(mp.glob("*.key"))
                dst.write_bytes(keyfile.read_bytes())
                dst.chmod(0o600)
            finally:
                run(["umount", str(mp)], check=False)
        finally:
            run(["cryptsetup", "close", name], check=False)
        return dst

    def btrfs_aliases(self) -> list[str]:
        """Where a mounted btrfs subvolume shows up a second time, as a
        subvolume nested in another one mounted: openSUSE's / is
        @/.snapshots/1/snapshot, which /.snapshots (@/.snapshots) shows
        again at /.snapshots/1/snapshot.  The backups leave those out:
        backed up twice, the two copies would be restored onto the same
        files (in the archivers' eyes hard links, inode for inode), which
        breaks the restores.  The restored layout shows the subvolume there
        again; fsmeta.py leaves it out too (see skip_aliases()).  Absolute
        paths below SRC_MP."""
        btrfs = []
        for m in self.mounts:
            if m["FSTYPE"] == "btrfs":
                sub = next((o[len("subvol="):] for o in m["OPTIONS"].split(",") if o.startswith("subvol=")), None)
                if sub is None:
                    raise SystemExit(f"findmnt names no subvol= for the btrfs at {m['TARGET']}")
                btrfs.append((os.path.realpath(m["SOURCE"].split("[")[0]), sub.strip("/"), m["TARGET"]))
        targets = [m["TARGET"] for m in self.mounts]
        aliases = []
        for dev, outer, at in btrfs:
            for dev2, inner, _ in btrfs:
                if dev2 != dev or inner == outer or not (outer == "" or inner.startswith(outer + "/")):
                    continue
                alias = os.path.join(at, inner[len(outer):].strip("/"))
                # Not where a mount below at's covers it (or a directory above it).
                if any(t != at and t.startswith(at.rstrip("/") + "/") and (alias == t or alias.startswith(t + "/"))
                       for t in targets):
                    continue
                aliases.append(alias)
        return sorted(set(aliases))

    def btrfs_subvolumes(self) -> tuple[list[str], str | None]:
        """The subvolumes of the btrfs at / and its default one (openSUSE's
        snapper boots from @/.snapshots/1/snapshot, which fstab doesn't
        name); a btrfs elsewhere isn't handled."""
        btrfs = {m["SOURCE"].split("[")[0] for m in self.mounts if m["FSTYPE"] == "btrfs"}
        if not btrfs:
            return [], None
        if self.mounts[0]["FSTYPE"] != "btrfs" or len(btrfs) > 1:
            raise SystemExit("recreate() handles btrfs only as the one file system at /")
        with src_api():
            listing = out(["chroot", str(tools_root(SRC_MP)), "btrfs", "subvolume", "list", "-a", "/"])
            default = out(["chroot", str(tools_root(SRC_MP)), "btrfs", "subvolume", "get-default", "/"])
        path = default.split(" path ", 1)[1].replace("<FS_TREE>/", "") if " path " in default else None
        return [line.split(" path ")[-1].replace("<FS_TREE>/", "") for line in listing.splitlines()], path


def in_src(cmd: list[str], root: Path = SRC_MP) -> None:
    """Run a tool from the original system (its mkfs etc.), or from the
    system at root (on ostree, its default deployment: tools_root()); see
    src_api()."""
    run(["chroot", str(tools_root(root))] + cmd)


class src_api:
    """The host's /dev, /proc and /sys bound into the (read-only) src tree,
    or the tree at root, so that tools run there with in_src() see the disks."""

    def __init__(self, root: Path = SRC_MP):
        self.root = tools_root(root)

    def __enter__(self):
        for d in ("dev", "proc", "sys"):
            run(["mount", "--bind", f"/{d}", str(self.root / d)], quiet=True)

    def __exit__(self, *exc):
        for d in ("sys", "proc", "dev"):
            run(["umount", str(self.root / d)], check=False, quiet=True)


class stick_root:
    """The rescue stick's root filesystem at STICK_MP, read-only: the stick
    image's two squashfs layers (the slimmed Ubuntu base and BackOtter's layer
    on top) under an overlay, as its live system mounts them."""

    PARTS = Path(f"/mnt/{PREFIX}-stick-parts{SUFFIX}")

    def __init__(self, img: Path):
        self.img = img
        self.mounted: list[Path] = []

    def mount(self, args: list[str], mp: Path) -> None:
        mp.mkdir(parents=True, exist_ok=True)
        run(["mount"] + args + [str(mp)])
        self.mounted.append(mp)

    def __enter__(self) -> Path:
        iso, base, layer = self.PARTS / "iso", self.PARTS / "base", self.PARTS / "layer"
        try:
            self.mount(["-o", "loop,ro", str(self.img)], iso)
            casper = iso / "casper"
            layers = sorted(casper.glob("*.backotter.squashfs"))
            if not layers:
                raise SystemExit(f"{self.img} is not a BackOtter stick: no casper/*.backotter.squashfs")
            self.mount(["-o", "loop,ro", str(casper / layers[0].name.replace(".backotter", ""))], base)
            self.mount(["-o", "loop,ro", str(layers[0])], layer)
            # No upperdir: a read-only overlay.
            self.mount(["-t", "overlay", "-o", f"lowerdir={layer}:{base}", "overlay"], STICK_MP)
        except BaseException:
            self.__exit__()           # with doesn't, when __enter__ fails
            raise
        return STICK_MP

    def __exit__(self, *exc):
        for mp in reversed(self.mounted):
            run(["umount", str(mp)], check=False, quiet=True)
        self.mounted = []


class Dst:
    """The restored disks, attached, and what recreate() set up on them, for
    unmount_dst() to undo: src's block devices -> theirs, and the LUKS
    volumes opened, md arrays made and pool imported."""

    def __init__(self, images: list[Path]):
        self.disks: list[str] = []
        self.dev: dict[str, str] = {}
        self.luks: list[str] = []
        self.md: list[str] = []
        self.vgs: list[tuple[str, str, str, Path]] = []    # (its name here, the guest's, its PVs, src's metadata)
        self.pool = False
        try:
            for image in images:
                self.disks.append(attach(image))
        except BaseException:
            for d in self.disks:
                detach(d)
            raise


def mkfs_cmd(f: dict[str, str], dev: str) -> list[str] | None:
    """The mkfs command that remakes the filesystem (or swap) blkid's fields
    f describe on dev, with its UUID and label; None for what isn't one."""
    fs, uuid, label = f.get("TYPE"), f.get("UUID"), f.get("LABEL")
    lab = ["-L", label] if label else []
    if fs in ("ext4", "ext3", "ext2"):
        return [f"mkfs.{fs}", "-q", "-F", "-U", uuid] + lab + [dev]
    if fs == "vfat":
        # An EFI system partition; FAT's "UUID" is its volume id.
        return ["mkfs.vfat", "-i", uuid.replace("-", "")] + (["-n", label] if label else []) + [dev]
    if fs == "f2fs":
        return ["mkfs.f2fs", "-q", "-f", "-U", uuid] + (["-l", label] if label else []) + [dev]
    if fs == "xfs":
        return ["mkfs.xfs", "-q", "-f", "-m", f"uuid={uuid}"] + lab + [dev]
    if fs == "btrfs":
        return ["mkfs.btrfs", "-q", "-f", "-U", uuid] + lab + [dev]
    if fs == "bcachefs":
        # In the original's on-disk version, which the guest keeps
        # (version_upgrade=none), not the tools' newest or the running
        # kernel's; and with its block size (mkfs.bcachefs picks 4096 on an
        # nbd device, where the original may have 512: its holes then
        # can't come back).
        return ["mkfs.bcachefs", "-f", "-U", uuid, f"--version={f['BCACHEFS_VERSION']}",
                f"--block_size={f['BCACHEFS_BLOCK_SIZE']}"] + lab + [dev]
    return None


def lvm_metadata_as(text: str, vg: str, name: str) -> str:
    """vgcfgbackup's text for vg, as volume group name with new VG and LV
    UUIDs (its PVs keep theirs)."""
    chars = string.ascii_letters + string.digits

    def new_id() -> str:
        u = "".join(secrets.choice(chars) for _ in range(32))
        return "-".join(u[a:b] for a, b in ((0, 6), (6, 10), (10, 14), (14, 18), (18, 22), (22, 26), (26, 32)))
    lines, stack, renamed = [], [], False
    for line in text.splitlines():
        if m := re.fullmatch(r"(\s*)(\S+) \{", line):
            if not stack and m[2] == vg:
                line, renamed = f"{m[1]}{name} {{", True
            stack.append(m[2])
        elif line.strip() == "}":
            stack.pop()
        elif stack and "physical_volumes" not in stack and (m := re.fullmatch(r'(\s*)id = "[^"]+"', line)):
            line = f'{m[1]}id = "{new_id()}"'
        lines.append(line)
    if not renamed:
        raise SystemExit(f"vgcfgbackup's text has no section for volume group {vg}")
    return "\n".join(lines) + "\n"


def luks_format(luks: dict, dev: str, passphrase: bytes) -> None:
    """A LUKS2 volume on dev like the original: its UUID, label, cipher, key
    size, sector size and key derivation (its memory cost matters: the
    guest's initramfs must afford it at boot)."""
    dump = luks["dump"]
    segment = dump["segments"]["0"]
    slot = dump["keyslots"][min(dump["keyslots"], key=int)]
    kdf = slot["kdf"]
    cmd = ["cryptsetup", "luksFormat", "--batch-mode", "--type", "luks2", "--uuid", luks["uuid"],
           "--cipher", segment["encryption"], "--key-size", str(slot["key_size"] * 8),
           "--sector-size", str(segment["sector_size"]), "--pbkdf", kdf["type"], "--key-file", "-"]
    if luks["label"]:
        cmd += ["--label", luks["label"]]
    if kdf["type"] == "pbkdf2":
        cmd += ["--hash", kdf["hash"], "--pbkdf-force-iterations", str(kdf["iterations"])]
    else:
        cmd += ["--pbkdf-memory", str(kdf["memory"]), "--pbkdf-parallel", str(kdf["cpus"]),
                "--pbkdf-force-iterations", str(kdf["time"])]
    run(cmd + [dev], input=passphrase.decode())


def recreate(layout: Layout, dst: Dst, cfg: GuestConfig, tools: Path = SRC_MP) -> None:
    """Lay out dst's disks like src's and mount them at DST_MP.  The
    filesystems are made by the mkfs tools of the system at tools: src's
    own, or the stick's."""
    for src_disk, dst_disk in zip(layout.disks, dst.disks):
        run(f"dd if={src_disk} of={dst_disk} bs=1M count=1 status=none conv=fsync")
        if out(["blkid", "-p", "-o", "value", "-s", "PTTYPE", src_disk], check=False) == "gpt":
            # The first MiB has GPT's primary header and table; its backup copy
            # belongs in the disk's last sectors, which firmware may check.
            run(["sfdisk", "-q", "--relocate", "gpt-bak-std", dst_disk])
        if GROW_GIB:
            last = max(int(p.rsplit("p", 1)[1]) for p, i in layout.parts.items() if layout.disks[i] == src_disk)
            run(["sfdisk", "-q", "--no-reread", "-N", str(last), dst_disk], input=", +\n")
        run(["partprobe", dst_disk], check=False)
    run(["udevadm", "settle"], quiet=True)
    for p, i in layout.parts.items():
        dst.dev[p] = dst.disks[i] + p[len(layout.disks[i]):]
        # GRUB's core image, on BIOS + GPT: no file system, so no backup has it.
        if out(["blkid", "-p", "-o", "value", "-s", "PART_ENTRY_TYPE", p], check=False).lower() == BIOS_BOOT:
            run(["dd", f"if={p}", f"of={dst.dev[p]}", "bs=1M", "status=none", "conv=fsync"])
    # Arrays on partitions, then LUKS volumes on either.
    for n, (src, md) in enumerate(layout.md.items()):
        dev = f"/dev/md/{DST_NAME}-{n}"
        host, _, name = md["name"].rpartition(":")
        # A name without a host part stays without one (not this host's).
        run(["mdadm", "--create", dev, "--run", "--assume-clean", f"--metadata={md['version']}",
             f"--level={md['level']}", f"--raid-devices={md['devices']}", f"--uuid={md['uuid']}",
             f"--name={name}", f"--homehost={host or '<none>'}"]
            + [dst.dev.get(m, m) for m in md["members"]])
        # The array by its kernel name, found through a member: the
        # /dev/md/NAME link needn't appear (an array of the same UUID this
        # host's udev assembled from someone else's disks takes it), and
        # unmount_dst() must stop the array either way.
        member = next(dst.dev[m] for m in md["members"] if m != "missing")
        run(["udevadm", "settle"], quiet=True)
        holders = sorted(Path(f"/sys/class/block/{Path(member).name}/holders").glob("md*"))
        if not holders:
            raise SystemExit(f"mdadm --create {dev} made no array on {member}")
        kdev = f"/dev/{holders[0].name}"
        dst.md.append(kdev)
        dst.dev[src] = kdev
    for n, (src, luks) in enumerate(layout.luks.items()):
        name = f"{DST_NAME}-luks{n}"
        luks_format(luks, dst.dev[luks["on"]], cfg.passphrase())
        run(["cryptsetup", "open", "--key-file", "-", dst.dev[luks["on"]], name], input=cfg.passphrase().decode())
        dst.luks.append(name)
        dst.dev[src] = f"/dev/mapper/{name}"
    run(["udevadm", "settle"], quiet=True)
    # Volume groups on any of those: src's metadata restored (the same PVs
    # with their UUIDs, the same extents), but while src's group is active
    # here, under a name and VG and LV UUIDs of their own: device-mapper
    # knows an active LV by its UUIDs, and would hand over src's.
    # unmount_dst() writes src's metadata back once dst's is inactive.
    for n, (vg, lv) in enumerate(layout.lvm.items()):
        temp = f"{DST_NAME}-vg{n}"
        backup = lv["backup"].with_suffix(".dst.vg")
        backup.write_text(lvm_metadata_as(lv["backup"].read_text(), vg, temp))
        pvs = {os.path.realpath(dst.dev[p]): u for p, u in lv["pvs"].items()}
        opts = LVM_OPTS + ["--devices", ",".join(pvs)]
        for dev, uuid in pvs.items():
            run(["pvcreate", "-q", "-y"] + opts + ["--uuid", uuid, "--restorefile", str(backup), dev])
        run(["vgcfgrestore", "-q"] + opts + ["-f", str(backup), temp])
        run(["vgchange", "-q"] + opts + ["-a", "y", temp])
        dst.vgs.append((temp, vg, ",".join(pvs), lv["backup"]))
        for name, src in lv["lvs"].items():
            path = f"/dev/mapper/{temp.replace('-', '--')}-{name.replace('-', '--')}"
            wait_for(path)
            dst.dev[src] = os.path.realpath(path)
    run(["udevadm", "settle"], quiet=True)
    with src_api(tools):
        def mkfs(cmd: list[str]) -> None:
            if run(["chroot", str(tools_root(tools)), "sh", "-c", f"command -v {cmd[0]}"], check=False, quiet=True).returncode:
                raise SystemExit(f"no {cmd[0]} in {tools}" + (" (the stick)" if tools != SRC_MP else ""))
            in_src(cmd, tools)
        for src, f in layout.fields.items():
            fs, dev = f.get("TYPE"), dst.dev[src]
            if fs in (None, "linux_raid_member", "crypto_LUKS", "LVM2_member", "zfs_member"):
                continue                    # nothing, or the arrays, volumes, groups and pool above and below
            if fs == "nilfs2":
                mkfs(["mkfs.nilfs2", "-q", "-f"] + (["-L", f["LABEL"]] if f.get("LABEL") else []) + [dev])
                mkfs(["nilfs-tune", "-U", f["UUID"], dev])
            elif fs == "swap":
                # The host's: swap's format is the same everywhere, and
                # busybox's mkswap (Alpine's) can't set the UUID.
                run(["mkswap", "-U", f["UUID"]] + (["-L", f["LABEL"]] if f.get("LABEL") else []) + [dev])
            elif fs == "jfs":
                mkfs(["mkfs.jfs", "-q"] + (["-L", f["LABEL"]] if f.get("LABEL") else []) + [dev])
                mkfs(["jfs_tune", "-U", f["UUID"], dev])
            elif cmd := mkfs_cmd(f, dev):
                mkfs(cmd)
            else:
                raise SystemExit(f"don't know how to recreate {fs}")
    if layout.zfs:
        recreate_pool(layout.zfs, dst)
    subvols, default_subvol = layout.btrfs_subvolumes()

    # Mount like src: each mount's device carries over.  A ZFS root first
    # (Ubuntu's is canmount=noauto), then the datasets that mount by
    # themselves, parents first.
    if layout.zfs:
        z = layout.zfs
        root_ds = next(m["SOURCE"] for m in layout.mounts if Path(m["TARGET"]) == SRC_MP)
        # check=False: zpool create mounted the pool's own dataset already,
        # where it is canmount=on.
        run(["zfs", "mount", DST_POOL_TEMP + root_ds[len(z["temp"]):]], check=False)
        for ds in sorted(z["datasets"], key=lambda d: z["info"][d].get("mountpoint", "")):
            i = z["info"][ds]
            if ds != root_ds and i["type"] == "filesystem" and i.get("canmount") == "on" \
                    and i.get("mountpoint", "none").startswith("/"):
                run(["zfs", "mount", DST_POOL_TEMP + ds[len(z["temp"]):]], check=False)
    for m in layout.mounts:
        target = DST_MP / os.path.relpath(m["TARGET"], SRC_MP)
        if m["FSTYPE"] == "zfs":
            continue
        dev = dst.dev[os.path.realpath(m["SOURCE"].split("[")[0])]
        if m["FSTYPE"] == "btrfs" and subvols:
            top = Path(f"/mnt/{PREFIX}-btrfs-top{SUFFIX}")
            top.mkdir(exist_ok=True)
            run(["mount", "-t", "btrfs", "-o", "subvolid=5", dev, str(top)])
            try:
                at = bind_point(tools_root(SRC_MP))
                run(["mount", "--bind", str(top), str(tools_root(SRC_MP) / at)])
                try:
                    with src_api():
                        for sv in subvols:
                            # A subvolume inside another (Fedora's root/var/
                            # lib/portables) needs the directories above it;
                            # the restore gives them their metadata.
                            (top / sv).parent.mkdir(parents=True, exist_ok=True)
                            in_src(["btrfs", "subvolume", "create", f"/{at}/{sv}"])
                        if default_subvol:
                            in_src(["btrfs", "subvolume", "set-default", f"/{at}/{default_subvol}"])
                finally:
                    run(["umount", str(tools_root(SRC_MP) / at)], check=False)
            finally:
                run(["umount", str(top)], check=False)
            subvols = []
        opts = [o for o in m["OPTIONS"].split(",") if o.startswith("subvol=")]
        # btrfs's compression, as the guest mounts it (vm-mount didn't):
        # a restore without it may not fit.
        if m["FSTYPE"] == "btrfs":
            rel = os.path.relpath(m["TARGET"], SRC_MP)
            opts += [o for o in layout.fstab.get("/" if rel == "." else "/" + rel, []) if o.startswith("compress")]
        if m["FSTYPE"] == "xfs":
            opts.append("nouuid")    # src, with the same UUID, is mounted too
        target.mkdir(parents=True, exist_ok=True)
        # The type explicitly: right after mkfs, mount can't always probe it.
        run(["mount", "-t", m["FSTYPE"]] + (["-o", ",".join(opts)] if opts else []) + [dev, str(target)])
        # Some guests' mkfs.nilfs2 (Zorin's) leaves an empty /.nilfs in the
        # new file system, which the original, made by another one, hasn't:
        # not the restore's, so not in the comparison.
        stray = target / ".nilfs"
        if m["FSTYPE"] == "nilfs2" and not os.path.lexists(Path(m["TARGET"]) / ".nilfs") \
                and stray.is_file() and not stray.is_symlink() and stray.stat().st_size == 0:
            stray.unlink()


def recreate_pool(z: dict, dst: Dst) -> None:
    """The pool, its vdevs on dst's devices, and its datasets: their local
    properties, their encryption (with the key src's were unlocked with,
    their keylocation as the original's once made), and its volumes' data,
    copied."""
    info = z["info"]

    def enc(ds: str, flag: str = "-o") -> list[str]:
        """Options for creating ds: its encryption, if it is an encryption
        root, or off, where its parent's is on and its own isn't."""
        i, o = info[ds], {}
        if i["encryption"] != "off" and i["encryptionroot"] == ds:
            o = {"encryption": i["encryption"], "keyformat": i["keyformat"], "keylocation": f"file://{z['key']}"}
            if i["keyformat"] == "passphrase":
                o["pbkdf2iters"] = i["pbkdf2iters"]
        elif i["encryption"] == "off" and info.get(ds.rsplit("/", 1)[0], i)["encryption"] != "off":
            o = {"encryption": "off"}
        return [a for k, v in o.items() for a in (flag, f"{k}={v}")]

    def name(ds: str) -> str:
        return DST_POOL_TEMP + ds[len(z["temp"]):]

    def created(ds: str, flag: str = "-o") -> list[str]:
        """normalization, utf8only and casesensitivity, which only creation
        sets, where ds's differ from its parent's (all of them, for the
        pool's own)."""
        parent = info.get(ds.rsplit("/", 1)[0]) if "/" in ds else None
        return [a for k in ("normalization", "utf8only", "casesensitivity")
                if info[ds]["type"] == "filesystem" and info[ds].get(k, "-") != "-"
                and (parent is None or parent.get(k) != info[ds][k])
                for a in (flag, f"{k}={info[ds][k]}")]

    cmd = ["zpool", "create", "-f", "-d", "-R", str(DST_MP), "-t", DST_POOL_TEMP]
    cmd += [a for feat in z["features"] for a in ("-o", f"{feat}=enabled")]
    cmd += [a for k, v in z["pool_props"].items() for a in ("-o", f"{k}={v}")]
    cmd += [a for k, v in z["props"][z["temp"]].items() for a in ("-O", f"{k}={v}")]
    cmd += enc(z["temp"], "-O") + created(z["temp"], "-O")
    vdevs = [a if a.startswith(("mirror", "raidz", "draid")) else dst.dev[a] for v in z["vdevs"] for a in v]
    run(cmd + [z["name"]] + vdevs)
    dst.pool = True
    for ds in z["datasets"][1:]:
        props = [a for k, v in z["props"][ds].items() for a in ("-o", f"{k}={v}")]
        if info[ds]["type"] == "volume":
            props = ["-V", info[ds]["volsize"], "-b", info[ds]["volblocksize"]] + props
        else:
            # Mounted below, parents first, once all are made.
            props = ["-u"] + props
        run(["zfs", "create"] + enc(ds) + created(ds) + props + [name(ds)])
    for ds in z["datasets"]:
        if info[ds]["type"] == "volume":
            wait_for(f"/dev/zvol/{name(ds)}")
            run(["dd", f"if=/dev/zvol/{ds}", f"of=/dev/zvol/{name(ds)}", "bs=1M", "status=none", "conv=fsync"])
        if info[ds]["encryption"] != "off" and info[ds]["encryptionroot"] == ds:
            run(["zfs", "set", f"keylocation={info[ds]['keylocation']}", name(ds)])


def retarget_zfs_boot(layout: Layout) -> None:
    """What a ZFS root's restore must do once its files are back: the
    restored /etc/zfs/zpool.cache, and the initramfs's copy of it, name the
    original pool's GUID, which the new pool hasn't, so dracut's
    zfs-import-cache.service imports nothing ("Cannot get properties of
    rpool: no such pool available"), and zfs-import-scan.service runs only
    without a cache file.  So: the new pool's cache file, and the initramfs
    rebuilt with it, by the restored system's own tool."""
    cache = DST_MP / "etc/zfs/zpool.cache"
    if not layout.zfs or not cache.is_file():
        return
    tmp = Path(f"/run/{DST_NAME}-zpool.cache")
    tmp.unlink(missing_ok=True)
    run(["zpool", "set", f"cachefile={tmp}", DST_POOL_TEMP])
    for _ in range(10):                        # ZFS writes it a moment later
        if tmp.exists() and tmp.stat().st_size:
            break
        time.sleep(1)
    else:
        raise SystemExit(f"ZFS wrote no {tmp} for {DST_POOL_TEMP}")
    shutil.copyfile(tmp, cache)
    run(["zpool", "set", "cachefile=none", DST_POOL_TEMP])
    tmp.unlink()
    with src_api(DST_MP):
        for cmd in (["update-initramfs", "-u", "-k", "all"], ["dracut", "--force", "--regenerate-all"],
                    ["mkinitcpio", "-P"]):
            if run(["chroot", str(DST_MP), "sh", "-c", f"command -v {cmd[0]}"], check=False, quiet=True).returncode == 0:
                in_src(cmd, DST_MP)
                break
        else:
            raise SystemExit("no initramfs tool in the restored system")


def unmount_dst(dst: Dst) -> None:
    run(["umount", "-R", str(DST_MP)], check=False)
    if dst.pool:
        # Never detach the disks under a pool still imported: its next I/O
        # fails, the pool suspends, and everything touching ZFS on the host
        # hangs with it.  A busy export gets a few more tries, then -f; if
        # the pool is still there, the disks stay attached and the run ends.
        for attempt in range(4):
            if run(["zpool", "list", "-H", "-o", "name", DST_POOL_TEMP], check=False, quiet=True).returncode:
                break                       # exported (or never imported)
            if attempt:
                time.sleep(5)
                run(["umount", "-R", str(DST_MP)], check=False, quiet=True)
            run(["zpool", "export"] + (["-f"] if attempt == 3 else []) + [DST_POOL_TEMP], check=False)
        else:
            if run(["zpool", "list", "-H", "-o", "name", DST_POOL_TEMP], check=False, quiet=True).returncode == 0:
                raise SystemExit(f"zpool export {DST_POOL_TEMP} failed: its disks ({', '.join(dst.disks)}) "
                                 "are left attached; export the pool, then qemu-nbd -d them")
    # The volume groups get src's metadata back, names and UUIDs, which the
    # guest's fstab and initramfs know them by; src's own are seen by none
    # of these commands.  vgcfgrestore finds src's LVs, active by those
    # UUIDs, and asks whether to go on: --force --yes, as it writes dst's
    # metadata only, and src's devices stay as they are.
    for temp, vg, pvs, backup in reversed(dst.vgs):
        opts = LVM_OPTS + ["--devices", pvs]
        for cmd in (["vgchange", "-q"] + opts + ["-a", "n", temp],
                    ["vgcfgrestore", "-q", "--force", "--yes"] + opts + ["-f", str(backup), vg]):
            r = run(cmd, check=False, stdin=subprocess.DEVNULL)
            if r.returncode:
                print(f"  failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-2000:]}", flush=True)
                break
    for name in reversed(dst.luks):
        run(["cryptsetup", "close", name], check=False)
    for md in reversed(dst.md):
        run(["mdadm", "--stop", md], check=False)
    for d in dst.disks:
        detach(d)


# --- The archivers ------------------------------------------------------------------

def restic_exe(tool: str) -> str:
    if tool == "restic":
        return "restic"
    if "RESTIC_BKO" in os.environ:
        return os.environ["RESTIC_BKO"]
    builds = sorted((WORK_BASE / "restic" / "dist").glob("restic-backotter-*-linux-amd64"),
                    key=lambda p: p.stat().st_mtime)
    if not builds:
        raise SystemExit("no restic-bko build: set RESTIC_BKO, or run build-backotter.sh")
    return str(builds[-1])


def rustic_exe() -> str:
    if "RUSTIC_BKO" in os.environ:
        return os.environ["RUSTIC_BKO"]
    # musl since 2026-11-02 (a static glibc build dies dlopening NSS for an
    # owner with no name); the newest of either.
    dist = WORK_BASE / "rustic" / "dist"
    builds = sorted([*dist.glob("rustic-backotter-*-x86_64-unknown-linux-musl"),
                     *dist.glob("rustic-backotter-*-x86_64-unknown-linux-gnu")],
                    key=lambda p: p.stat().st_mtime)
    if not builds:
        raise SystemExit("no rustic-bko build: set RUSTIC_BKO, or run build-backotter.sh")
    return str(builds[-1])


# Back up with one of BackOtter's two programs, restore with the other.
CROSS = {"restic-to-rustic": ("restic-bko", "rustic-bko"),
         "rustic-to-restic": ("rustic-bko", "restic-bko")}


def glob_escape(path: str) -> str:
    """path as a glob pattern matching itself (restic's, rustic's)."""
    return re.sub(r"([][*?\\])", r"\\\1", path)


def backup(tool: str, work: Path, excludes: list[str]) -> None:
    """tool's backup of SRC_MP into work/tool, without excludes (absolute
    paths below SRC_MP, and what is below them)."""
    repo = work / tool
    tool = CROSS.get(tool, (tool, tool))[0]
    rel = [os.path.relpath(e, SRC_MP) for e in excludes]
    shutil.rmtree(repo, ignore_errors=True)
    env = dict(os.environ, RESTIC_PASSWORD=PASSWORD, RUSTIC_PASSWORD=PASSWORD, BORG_PASSPHRASE=PASSWORD)
    if tool in ("restic", "restic-bko"):
        run([restic_exe(tool), "-r", str(repo), "init", "-q"], env=env)
        # BackOtter's flags: the real atime (--with-atime; the forks keep
        # stock's default, the mtime as the atime).  Stock restic as stock.
        run([restic_exe(tool), "-r", str(repo), "backup", "-q"]
            + (["--with-atime"] if tool == "restic-bko" else [])
            + [a for e in excludes for a in ("--exclude", glob_escape(e))] + [str(SRC_MP)], env=env)
    elif tool == "rustic-bko":
        # BackOtter's flags: the real atime (rustic-bko keeps stock rustic's
        # default, the mtime as the atime).
        rustic = [rustic_exe(), "-r", str(repo), "--no-progress", "--no-cache"]
        run(rustic + ["init"], env=env)
        run(rustic + ["backup", "--set-atime", "yes"]
            + [a for e in excludes for a in ("--glob", "!" + glob_escape(e))]
            + [str(SRC_MP)], env=env)
    elif tool == "borg":
        run(["borg", "init", "-e", "repokey-blake2", str(repo)], env=env)
        run(["borg", "create", "--numeric-ids", "-C", "zstd,3"]
            + [a for r in rel for a in ("--exclude", f"pp:{r}")] + [f"{repo}::backup", "."], env=env, cwd=SRC_MP)
    elif tool == "dar":
        repo.mkdir()
        run(["dar", "-c", str(repo / "backup"), "-R", str(SRC_MP), "-zgzip:6", "-Q", "-q", "-w"]
            + [a for r in rel for a in ("-P", r)])
    elif tool == "bsdtar":
        # pax format, with ACLs, every xattr and the inode flags (bsdtar's
        # "fflags"); it crosses into /boot and the other mounts, as borg does.
        repo.mkdir()
        run(["bsdtar", "-c", "-f", str(repo / "backup.tar.zst"), "--zstd", "--format", "pax",
             "--numeric-owner", "--acls", "--xattrs", "--fflags"]
            # ^: anchored, or it would leave out each path ending so.
            + [a for r in rel for a in ("--exclude", glob_escape(f"^./{r}"))] + ["-C", str(SRC_MP), "."])


def restore(tool: str, work: Path) -> None:
    repo = work / tool
    tool = CROSS.get(tool, (tool, tool))[1]
    env = dict(os.environ, RESTIC_PASSWORD=PASSWORD, RUSTIC_PASSWORD=PASSWORD, BORG_PASSPHRASE=PASSWORD)
    if tool == "restic":
        run([restic_exe(tool), "-r", str(repo), "restore", "-q", "--sparse",
             f"latest:{SRC_MP}", "--target", str(DST_MP)], env=env)
    elif tool == "restic-bko":
        # No --sparse: BackOtter's restic makes holes in the files that had
        # them, and allocates the rest (swap files, preallocated files).
        run([restic_exe(tool), "-r", str(repo), "restore", "-q",
             f"latest:{SRC_MP}", "--target", str(DST_MP)], env=env)
    elif tool == "rustic-bko":
        # BackOtter's defaults: holes where there were holes.  Its flags:
        # numeric ids (--numeric-id; rustic-bko maps names by default, as
        # stock rustic does).
        run([rustic_exe(), "-r", str(repo), "--no-progress", "--no-cache", "restore", "--numeric-id",
             f"latest:{SRC_MP}", str(DST_MP)], env=env)
    elif tool == "borg":
        run(["borg", "extract", "--numeric-ids", "--sparse", f"{repo}::backup"], env=env, cwd=DST_MP)
    elif tool == "dar":
        run(["dar", "-x", str(repo / "backup"), "-R", str(DST_MP), "-Q", "-q", "-w"])
    elif tool == "bsdtar":
        # -S makes holes of runs of zeros, as restic's and borg's --sparse do.
        run(["bsdtar", "-x", "-f", str(repo / "backup.tar.zst"), "-p", "--same-owner",
             "--numeric-owner", "--acls", "--xattrs", "--fflags", "-S", "-C", str(DST_MP)])


def size(path: Path) -> int:
    return int(out(["du", "-sb", str(path)]).split()[0])


# --- Booting -------------------------------------------------------------------------

def port_free(port: str) -> bool:
    import socket
    with socket.socket() as s:
        # As QEMU binds its forwarded port: the last boot test's closed SSH
        # connections linger in TIME_WAIT for a minute, but don't stop QEMU.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def io_stall_s() -> float:
    """How long, in all, the host's tasks have all been stalled on I/O at
    once (PSI's io "full" total), in seconds; 0 without PSI."""
    try:
        for line in Path("/proc/pressure/io").read_text().splitlines():
            if line.startswith("full "):
                return int(line.rpartition("total=")[2]) / 1e6
    except (OSError, ValueError):
        pass
    return 0.0


# What the guest booted with: bios, uefi, or uefi+secure-boot.
FIRMWARE_PROBE = ("if [ -d /sys/firmware/efi ]; then "
                  "sb=$(od -An -tu1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
                  " 2>/dev/null | awk '{print $NF}'); "
                  "if [ \"$sb\" = 1 ]; then echo uefi+secure-boot; else echo uefi; fi; "
                  "else echo bios; fi")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
# A passphrase prompt on the serial console: Ubuntu's ZFS keystore's, or
# cryptsetup-initramfs's.
PROMPT = re.compile(rb"(?i)enter passphrase|please unlock disk [^\n]*:\s*$|geli passphrase for \S+\s*$")


class VM:
    """A guest booted from images by vm-run (with its writes in RAM, unless
    ram is False), its serial console logged to log, its disk passphrase
    typed where it asks for one; stopped on leaving the with block."""

    def __init__(self, images: list[Path], cfg: GuestConfig, hardware: list[str], log: Path, ram: bool = True):
        self.images, self.cfg, self.log = images, cfg, log
        self.cmd = [str(TESTVM / "vm-run")] + (["--ram"] if ram else []) + hardware
        self.cmd += [a for d in images[1:] for a in ("--disk", str(d))]
        self.monitor = log.with_name(log.name + ".monitor")
        self.ssh_cmd = ["ssh", "-q", "-i", str(cfg.dir / "id_ed25519"), "-p", SSH_PORT, "-o", "BatchMode=yes",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "ConnectTimeout=5", "root@127.0.0.1"]
        self.up = False
        self.typer = None

    def __enter__(self) -> VM:
        if not port_free(SSH_PORT):
            raise SystemExit(f"port {SSH_PORT} is taken (a test VM left running?)")
        # A restored disk's qemu-nbd may still be letting go of it (after
        # JFS, for one); vm-run can't open a locked image, and would quietly
        # give up.
        for image in self.images:
            for _ in range(30):
                if run(["qemu-img", "info", str(image)], check=False, quiet=True).returncode == 0:
                    break
                time.sleep(1)
        cmd, console = list(self.cmd), self.cfg.key_kind in ("zfs", "console")
        if not console:
            cmd += ["--serial-log", str(self.log)]
        cmd.append(str(self.images[0]))
        if self.cfg.key_kind == "luks":
            self.monitor.unlink(missing_ok=True)
            cmd += ["--", "-monitor", f"unix:{self.monitor},server,nowait"]
        # Its own process group, so that stopping it stops QEMU too, and a VM
        # left running can't hold SSH_PORT against the next boot test.  Its
        # overlays (--ram) beside the log, on disk: in /dev/shm, the host's
        # other VMs' overlays can leave no room.
        ram_dir = self.log.parent
        self.overlays = lambda: set(ram_dir.glob("vm-run.*.qcow2"))
        self.overlays_before = self.overlays()
        self.proc = subprocess.Popen(cmd, env=dict(os.environ, SSH_PORT=SSH_PORT, RAM_DIR=str(ram_dir)),
                                     start_new_session=True,
                                     stdin=subprocess.PIPE if console else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE if console else subprocess.DEVNULL,
                                     stderr=open(self.log.with_name(self.log.stem + ".vm-run.log"), "w"))
        if console:
            threading.Thread(target=self._console, daemon=True).start()
        if self.cfg.key_kind == "luks":
            # Nothing tells when Plymouth's prompt is up: typed every 30 s
            # until SSH answers (at a login prompt, it does no harm).
            self.typer = subprocess.Popen(["python3", str(TESTVM / "omarchy/type-luks-passphrase.py"),
                                           str(self.monitor), str(self.cfg.key),
                                           *[str(d) for d in range(25, 300, 30)]],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self

    def _console(self) -> None:
        """The serial console, into the log, with the passphrase typed at
        each prompt for it."""
        buf, last = b"", 0.0
        with open(self.log, "wb") as log:
            while chunk := self.proc.stdout.read1(4096):
                log.write(chunk)
                log.flush()
                buf = (buf + ANSI.sub(b"", chunk))[-4096:]
                if PROMPT.search(buf) and time.monotonic() - last > 5:
                    time.sleep(1)
                    try:
                        self.proc.stdin.write(self.cfg.passphrase() + b"\r")
                        self.proc.stdin.flush()
                    except OSError:
                        return
                    buf, last = b"", time.monotonic()

    def wait_ssh(self, timeout: float) -> bool:
        """Whether SSH answers within timeout seconds, plus the time the
        host spent stalled on I/O meanwhile (a loaded host's guest gets
        nowhere then), up to 3 x timeout in all."""
        start, stall0 = time.monotonic(), io_stall_s()
        self.io_stall = 0.0
        while self.proc.poll() is None:
            self.io_stall = io_stall_s() - stall0
            if time.monotonic() >= start + min(timeout + self.io_stall, 3 * timeout):
                break
            if run(self.ssh_cmd + ["true"], check=False, quiet=True).returncode == 0:
                self.up = True
                break
            time.sleep(3)
        if self.typer and self.typer.poll() is None:
            self.typer.terminate()
        return self.up

    def ssh(self, command: str, input: str | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
        return run(self.ssh_cmd + [command], check=False, quiet=True, input=input, timeout=timeout)

    def put(self, path: Path, dest: str) -> None:
        """Copy path to dest in the guest (over ssh with cat: no scp needed)."""
        r = run(self.ssh_cmd + [f"cat >{shlex.quote(dest)} && chmod +x {shlex.quote(dest)}"],
                check=False, quiet=True, input=path.read_text())
        if r.returncode:
            raise SystemExit(f"copying {path} into the guest failed: {r.stderr.strip()}")

    def __exit__(self, *exc):
        try:
            if self.up and self.proc.poll() is None:
                self.ssh("sync; poweroff", timeout=60)
                self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if self.typer and self.typer.poll() is None:
                self.typer.terminate()
            if self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    self.proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    self.proc.wait()
            # Killed, vm-run can't remove its overlays (QEMU blocked on a
            # stalled disk outlasts the 20 s); this run's only.
            for o in self.overlays() - self.overlays_before:
                o.unlink(missing_ok=True)


def boot_state(text: str) -> dict:
    """boot-state.sh's report, as a dict."""
    st: dict = {"init": "?", "state": "?", "failed_units": [], "services": {}}
    for line in text.splitlines():
        key, _, rest = line.partition(" ")
        if key in ("init", "state"):
            st[key] = rest
        elif key == "failed":
            st["failed_units"].append(rest)
        elif key == "service":
            name, _, s = rest.rpartition(" ")
            st["services"][name] = s
    return st


def boot(images: list[Path], cfg: GuestConfig, hardware: list[str], log: Path) -> dict:
    """Boot images (writes in RAM) and say how they came up: whether SSH
    answered, boot-state.sh's report, and the firmware.  Without SSH, a
    second boot, from the same disks (their writes were in RAM): a host
    stalled on its disk can hold a guest up for minutes.  "attempts" says
    how many it took, "io_stall_s" how long the host was stalled meanwhile;
    the first attempt's console is kept in LOG.try1."""
    for attempt in (1, 2):
        result = {"ssh": False, "attempts": attempt}
        try:
            with VM(images, cfg, hardware, log) as vm:
                if vm.wait_ssh(420 if cfg.key else 240):
                    result["ssh"] = True
                    r = vm.ssh("sh -s", input=BOOT_STATE.read_text(), timeout=400)
                    result.update(boot_state(r.stdout))
                    # On sh's stdin too: DragonFly root's login shell, csh, can't parse it.
                    result["firmware"] = vm.ssh("sh -s", input=FIRMWARE_PROBE, timeout=30).stdout.strip()
                result["io_stall_s"] = round(vm.io_stall)
        except subprocess.TimeoutExpired:
            pass
        if result["ssh"] or attempt == 2:
            return result
        print(f"   no ssh (host stalled on I/O {result.get('io_stall_s', '?')} s); booting again", flush=True)
        for f in (log, log.with_name(log.stem + ".vm-run.log")):
            if f.exists():
                f.replace(f.with_name(f.name + ".try1"))
    return result


def new_failures(b: dict, original: dict) -> list[str] | None:
    """What went wrong in a restore's boot b and not in the original's: its
    failed units that the original's aren't, its services in another state
    than the original's, and its still starting.  None if it didn't boot."""
    if not b.get("ssh"):
        return None
    new = [u for u in b.get("failed_units", []) if u not in original.get("failed_units", [])]
    for name, s in original.get("services", {}).items():
        mine = b.get("services", {}).get(name, "absent")
        if mine != s and name not in new:
            new.append(f"{name} ({s}, now {mine})")
    if b.get("state") == "starting" and original.get("state") != "starting":
        new.append("still starting")
    return new


def torture_in_guest(images: list[Path], cfg: GuestConfig, hardware: list[str], log: Path) -> dict:
    """torture.sh run by the guest itself, booted from images (src: its
    writes kept there), for a guest whose file systems this host can't
    mount read-write; acl and attr installed first where it has apt and
    lacks them.  Returns torture.sh's skips, and the guest's own view of its
    ZFS pools' features (this host may not know them all)."""
    with VM(images, cfg, hardware, log, ram=False) as vm:
        if not vm.wait_ssh(420 if cfg.key else 240):
            raise SystemExit(f"src didn't come up for torture.sh in the guest (console: {log})")
        r = vm.ssh("command -v apt-get >/dev/null || exit 0; command -v setfacl >/dev/null && "
                   "command -v setfattr >/dev/null && exit 0; "
                   "DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends acl attr")
        if r.returncode:
            print(f"  installing acl and attr in the guest failed: {r.stderr.strip()[-500:]}", flush=True)
        vm.put(HERE / "torture.sh", "/root/torture.sh")
        r = vm.ssh("bash /root/torture.sh / && rm /root/torture.sh && sync")
        if r.returncode:
            raise SystemExit(f"torture.sh in the guest failed: {r.stderr.strip()[-1500:]}")
        features = vm.ssh("command -v zpool >/dev/null && zpool get -H -o name,property,value all", timeout=60).stdout
    return {"skipped": [line for line in r.stderr.splitlines() if "skipped" in line],
            "zfs_features": sorted({line.split("\t")[1] for line in features.splitlines()
                                    if "\tfeature@" in line and line.split("\t")[2] in ("enabled", "active")})}


# --- One guest ----------------------------------------------------------------------

def skip_aliases(layout: Layout) -> list[str]:
    """fsmeta.py's options leaving out what the backups leave out (the
    second view of a btrfs subvolume, see btrfs_aliases()): below its
    mounts' directories, which the mounts hide where they are mounted, it
    shows the ones only mkdir made in a restore."""
    return [a for p in layout.aliases for a in ("--skip", os.path.relpath(p, SRC_MP))]


def work_dir(guest: str, stick_img: Path | None, parent: Path = COMPARE_DIR) -> Path:
    return parent / (guest + (".stick-mkfs" if stick_img else ""))


def restored_stem(work: Path, tool: str) -> Path:
    """Where TOOL's restored disks go: the work directory, or COMPARE_DISKS_DIR's."""
    return (DISKS_DIR / work.name if DISKS_DIR else work) / f"restored-{tool}"


def save_from_ram(guest: str, stick_img: Path | None) -> None:
    """A run in RAM_DIR: its results, diffs and logs to COMPARE_DIR, whatever
    the run's outcome, and the rest (disks, repositories) dropped."""
    ram, disk = work_dir(guest, stick_img, RUN_DIR), work_dir(guest, stick_img)
    if not IN_RAM or not ram.is_dir():
        return
    disk.mkdir(parents=True, exist_ok=True)
    for f in ram.iterdir():
        if f.is_file() and f.suffix != ".qcow2":
            shutil.copy2(f, disk / f.name)
    for d in (ram, DISKS_DIR / ram.name) if DISKS_DIR else (ram,):
        shutil.rmtree(d, ignore_errors=True)
        try:
            d.parent.rmdir()                    # the run's own, once empty
        except OSError:
            pass


def compare_guest(guest: str, tools: list[str], stick_img: Path | None = None) -> dict:
    cfg = GuestConfig(VMS / guest)
    bases = [cfg.base] + cfg.disks
    work = work_dir(guest, stick_img, RUN_DIR)
    if IN_RAM:
        shutil.rmtree(work, ignore_errors=True)    # a dead run's
        if DISKS_DIR:
            shutil.rmtree(DISKS_DIR / work.name, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    if DISKS_DIR:
        (DISKS_DIR / work.name).mkdir(parents=True, exist_ok=True)
    fsmeta = [sys.executable, "-I", str(HERE / "fsmeta.py")]
    results = {"guest": guest, "mkfs": str(stick_img) if stick_img else "guest", "tools": {},
               "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               # Which disks this run tested: an earlier run's results are
               # kept below only if it tested the same ones.
               "base": [{"path": str(b), "size": b.stat().st_size, "mtime": int(b.stat().st_mtime)}
                        for b in bases]}
    if GROW_GIB:
        results["grown_gib"] = GROW_GIB

    print(f"== {guest}: test files", flush=True)
    srcs = disk_images(work / "src", len(bases))
    for src, base in zip(srcs, bases):
        src.unlink(missing_ok=True)
        run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(base), "-F", "qcow2", str(src)])
    try:
        vm_mount(srcs, SRC_MP, ro=False, key=cfg.key)
    except subprocess.CalledProcessError:
        # Only where a read-only mount works (a pool with features this
        # host's OpenZFS lacks, an md superblock it can't assemble): any
        # other failure stays one.
        vm_umount(SRC_MP)
        vm_mount(srcs, SRC_MP, ro=True, key=cfg.key)
        vm_umount(SRC_MP)
        print(f"== {guest}: this host can't mount it read-write; torture.sh in the guest", flush=True)
        # Without vm-run-options, by BIOS: the layout isn't known yet.
        t = torture_in_guest(srcs, cfg, cfg.hardware({}), work / "torture-serial.log")
        results["torture_skipped"], results["torture_in_guest"] = t["skipped"], True
        guest_features = t["zfs_features"]
    else:
        try:
            r = run([str(HERE / "torture.sh"), str(SRC_MP)])
            results["torture_skipped"] = [l for l in r.stderr.splitlines() if "skipped" in l]
        finally:
            vm_umount(SRC_MP)
        guest_features = []
    # Let the nbd devices from that mount be released before attaching again.
    run(["udevadm", "settle"], quiet=True)
    time.sleep(2)

    state = vm_mount(srcs, SRC_MP, ro=True, key=cfg.key)
    try:
        layout = Layout(state, cfg.key, work)
        bcachefs = any(f.get("TYPE") == "bcachefs" for f in layout.fields.values())
        if bcachefs and not stick_img:
            raise SystemExit("a guest with bcachefs needs --mkfs-from: src can't stay mounted for its own mkfs")
        results["layout"] = {p: f.get("TYPE") for p, f in layout.fields.items()}
        if layout.aliases:
            results["not_backed_up"] = [os.path.relpath(a, SRC_MP) for a in layout.aliases]
        if layout.zfs and guest_features:
            results["zfs_features_not_recreated"] = sorted(set(guest_features) - set(layout.zfs["features"]))
        hardware = cfg.hardware(results["layout"])
        run(fsmeta + ["record", str(SRC_MP), str(work / "before.json")] + skip_aliases(layout))
        results["source_bytes"] = int(out(["du", "-sbx", str(SRC_MP)]).split()[0]) + sum(
            int(out(["du", "-sbx", m["TARGET"]]).split()[0]) for m in layout.mounts[1:])
    finally:
        vm_umount(SRC_MP)
    run(["udevadm", "settle"], quiet=True)
    time.sleep(2)
    print(f"== {guest}: booting the original (src)", flush=True)
    results["original_boot"] = boot(srcs, cfg, hardware, work / "boot-original.log")
    print(f"   {results['original_boot']}", flush=True)

    state = vm_mount(srcs, SRC_MP, ro=True, key=cfg.key)
    try:
        layout = Layout(state, cfg.key, work)
        def backup_one(tool: str) -> None:
            print(f"== {guest}: {tool} backup", flush=True)
            t = time.monotonic()
            backup(tool, work, layout.aliases)
            results["tools"][tool] = {"backup_s": round(time.monotonic() - t, 1),
                                      "backup_bytes": size(work / tool), "run": results["started"]}
        def restore_and_boot(tool: str) -> None:
            print(f"== {guest}: {tool} restore", flush=True)
            res = results["tools"][tool]
            dsts = disk_images(restored_stem(work, tool), len(srcs))
            for d, src in zip(dsts, srcs):
                d.unlink(missing_ok=True)
                vsize = json.loads(out(["qemu-img", "info", "--output=json", str(src)]))["virtual-size"] + (GROW_GIB << 30)
                run(["qemu-img", "create", "-q", "-f", "qcow2", str(d), str(vsize)])
            dst = Dst(dsts)
            try:
                if stick_img:
                    with stick_root(stick_img) as stick:
                        recreate(layout, dst, cfg, stick)
                else:
                    recreate(layout, dst, cfg)
                # Restoring into a DST_MP that isn't the new disk would write
                # onto the host's own filesystem.
                if run(["mountpoint", "-q", str(DST_MP)], check=False, quiet=True).returncode != 0:
                    raise SystemExit(f"{DST_MP} is not mounted after recreating the layout")
                t = time.monotonic()
                try:
                    restore(tool, work)
                    res["restore_ok"] = True
                except subprocess.CalledProcessError as e:
                    res["restore_ok"] = False
                    res["restore_error"] = (e.stderr or e.stdout or "")[-1500:]
                res["restore_s"] = round(time.monotonic() - t, 1)
                remount_noatime(DST_MP)
                run(fsmeta + ["record", str(DST_MP), str(work / f"after-{tool}.json")] + skip_aliases(layout))
                r = run(fsmeta + ["compare", str(work / "before.json"), str(work / f"after-{tool}.json"),
                                  "--json", str(work / f"diff-{tool}.json")], check=False)
                print(r.stdout, flush=True)
                res["diff"] = json.loads((work / f"diff-{tool}.json").read_text())["differences"]
                # Its failure is this restore's boot's, not the run's.
                try:
                    retarget_zfs_boot(layout)
                except (subprocess.CalledProcessError, SystemExit) as e:
                    res["retarget_error"] = str(e)[-1500:]
                    print(f"   retarget_zfs_boot failed: {res['retarget_error']}", flush=True)
            finally:
                unmount_dst(dst)
            if DISCARD:
                shutil.rmtree(work / tool, ignore_errors=True)
            print(f"== {guest}: booting the {tool} restore", flush=True)
            res["boot"] = boot(dsts, cfg, hardware, work / f"boot-{tool}.log")
            res["boot"]["new_failures"] = new_failures(res["boot"], results["original_boot"])
            print(f"   {res['boot']}", flush=True)
            if DISCARD:
                for d in dsts:
                    d.unlink(missing_ok=True)
        if bcachefs:
            for tool in tools:
                backup_one(tool)
            # The kernel mounts no two bcachefs file systems with the same
            # UUID, so src goes before dst is mounted; its disk stays
            # attached for recreate(), which then needs only its first MiB.
            run(["umount", "-R", str(SRC_MP)])
            for tool in tools:
                restore_and_boot(tool)
        else:
            # One direction at a time, backed up, restored and booted: one
            # repository at once (and, with COMPARE_DISCARD, one restored
            # disk), where all four backups first would hold four.
            for tool in tools:
                backup_one(tool)
                restore_and_boot(tool)
        results["coverage"] = json.loads((work / f"diff-{tools[0]}.json").read_text())["coverage"]
    finally:
        vm_umount(SRC_MP)
    # Keep an earlier run's results for the tools this run left out, so one
    # tool can be rerun alone (torture.sh makes the same files each time) --
    # if that run tested the same disks: a rebuilt guest's are another test.
    old = work_dir(guest, stick_img) / "results.json"
    if old.exists():
        prev = json.loads(old.read_text())
        earlier = {t: v for t, v in prev.get("tools", {}).items() if t not in tools}
        if earlier and prev.get("base") == results["base"]:
            for v in earlier.values():
                v.setdefault("run", prev.get("started", "?"))
            results["tools"] = earlier | results["tools"]
        elif earlier:
            print(f"== {guest}: dropping an earlier run's results for {', '.join(earlier)}: "
                  "it tested other disks (or didn't say which)", flush=True)
    (work / "results.json").write_text(json.dumps(results, indent=1))
    return results


def boot_only(guest: str, tools: list[str], stick_img: Path | None) -> dict:
    """Boot the disks a previous run restored, and update its results; the
    original too, if that run didn't (or src is all that is asked for)."""
    cfg = GuestConfig(VMS / guest)
    work = work_dir(guest, stick_img)
    r = json.loads((work / "results.json").read_text())
    n = 1 + len(cfg.disks)
    hardware = cfg.hardware(r["layout"])
    tools = tools or list(r["tools"])
    if "original_boot" not in r or "src" in tools:
        print(f"== {guest}: booting the original (src)", flush=True)
        r["original_boot"] = boot(disk_images(work / "src", n), cfg, hardware, work / "boot-original.log")
        print(f"   {r['original_boot']}", flush=True)
    for tool in tools:
        if tool == "src":
            continue
        if tool not in r["tools"]:
            raise SystemExit(f"the earlier run of {guest} restored no {tool}")
        print(f"== {guest}: booting the {tool} restore", flush=True)
        b = r["tools"][tool]["boot"] = boot(disk_images(restored_stem(work, tool), n), cfg, hardware,
                                             work / f"boot-{tool}.log")
        b["new_failures"] = new_failures(b, r["original_boot"])
        print(f"   {b}", flush=True)
    (work / "results.json").write_text(json.dumps(r, indent=1))
    return r


def remove_mount_points() -> None:
    """COMPARE_NAME's mount points, where nothing is mounted on them any
    more (rmdir removes only empty directories)."""
    parts = stick_root.PARTS
    for d in (parts / "iso", parts / "base", parts / "layer", parts, SRC_MP, DST_MP, STICK_MP,
              Path(f"/mnt/{PREFIX}-btrfs-top{SUFFIX}"), Path(f"/mnt/{DST_NAME}-keystore")):
        if d.is_dir() and not os.path.ismount(d):
            try:
                d.rmdir()
            except OSError:
                pass


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    args = sys.argv[1:]
    only_boot, stick_img = False, None
    while args and args[0].startswith("--"):
        if args[0] == "--boot-only":
            only_boot, args = True, args[1:]
        elif args[0] == "--mkfs-from" and len(args) > 1:
            stick_img, args = Path(args[1]).resolve(), args[2:]
            if not stick_img.is_file():
                raise SystemExit(f"no such image: {stick_img}")
        else:
            raise SystemExit(__doc__)
    if not args:
        raise SystemExit(__doc__)
    guest, tools = args[0], args[1:] or ([] if only_boot else ["restic", "borg", "dar", "bsdtar"])
    for m in (SRC_MP, DST_MP, STICK_MP):
        m.mkdir(parents=True, exist_ok=True)
    try:
        r = boot_only(guest, tools, stick_img) if only_boot else compare_guest(guest, tools, stick_img)
    finally:
        if not only_boot:
            save_from_ram(guest, stick_img)
        if NAME:
            remove_mount_points()
    o = r.get("original_boot", {})
    print(f"\noriginal boot: {o.get('state', 'no ssh') if o.get('ssh') else 'no ssh'} ({o.get('init', '?')})"
          + (f", failed: {', '.join(o['failed_units'])}" if o.get("failed_units") else ""))
    if r.get("zfs_features_not_recreated"):
        print(f"ZFS features this host couldn't give the restored pool: {', '.join(r['zfs_features_not_recreated'])}")
    print(f"\n{'tool':16} {'backup':>8} {'size':>9} {'restore':>8}  {'boot':9} {'new in boot':24} differences")
    notes = []
    for tool, t in r["tools"].items():
        diffs = ", ".join(f"{k} {v['count']}" for k, v in t.get("diff", {}).items()) or "none"
        b = t.get("boot", {})
        new = b.get("new_failures")
        new = "-" if new is None else ", ".join(new) or "none"
        name = tool
        if t.get("run") != r.get("started"):
            name += "*"
            notes.append(f"{tool}: from an earlier run ({t.get('run', '?')})")
        if b.get("attempts", 1) > 1:
            notes.append(f"{tool}: booted again after no ssh (host stalled on I/O {b.get('io_stall_s', '?')} s)")
        print(f"{name:16} {t['backup_s']:7}s {t['backup_bytes'] / 2**20:7.0f}MB {t.get('restore_s', 0):7}s  "
              f"{b.get('state', '?') if b.get('ssh') else 'no ssh':9} {new:24} {diffs}")
    if o.get("attempts", 1) > 1:
        notes.append(f"original: booted again after no ssh (host stalled on I/O {o.get('io_stall_s', '?')} s)")
    for n in notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
