#!/usr/bin/env python3
"""Back up a test guest with several archivers, restore each backup onto a
blank disk, and check what survived.

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
fresh-install state), in ~/backotter-work/compare/GUEST/:
  1. src.qcow2, a throwaway overlay on the guest's disk, gets torture.sh's
     hard-to-preserve files; the guest's own image is never written.
  2. Each tool backs up / and everything mounted below it (e.g. /boot), from
     src mounted read-only, as a backup taken from a rescue system would.
  3. Each backup is restored onto a blank 16 GiB disk laid out like the
     original: the same partition table and boot code (the first MiB, which
     holds GRUB's core image on these BIOS + MBR guests), and filesystems
     recreated with the same UUIDs and labels, by the guest's own mkfs tools
     (so the guest's GRUB and kernel can read them), btrfs subvolumes and ZFS
     pools (with exactly the original's feature flags) included.
  4. fsmeta.py compares every file's metadata before and after, and the
     restored disk is booted to see whether it comes up.
That is the archiver's part of a bare-metal restore; recreating a layout
from a recorded description, as BackOtter must, is not tested here.

--mkfs-from STICK_IMG makes the filesystems with the mkfs tools of BackOtter's
rescue stick (STICK_IMG, from stick/make-img.sh) instead of the guest's own,
as a restore from the stick would: newer tools turn on features an older
guest's kernel or GRUB may not read.  ZFS pools are still made by the host's
zpool, with the original's feature flags, as BackOtter must make them.  The
results go to ~/backotter-work/compare/GUEST.stick-mkfs/.

The restores boot with the vm-run options the guest's builder left in
vms/GUEST/vm-run-options (e.g. --uefi, with Secure Boot); without that file,
by BIOS, or by UEFI without Secure Boot if the guest has an EFI system
partition.  Each boot gets a fresh UEFI variable store, as a new machine's.

Results: results.json and a table on stdout.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK_BASE = HERE.parents[1]                       # ~/backotter-work
TESTVM = HERE.parent
VMS = WORK_BASE / "vms"
SRC_MP = Path("/mnt/cmp-src")
DST_MP = Path("/mnt/cmp-dst")
STICK_MP = Path("/mnt/cmp-stick")                 # --mkfs-from: the stick's root
SSH_PORT = "2230"
PASSWORD = "backotter-compare"                    # for restic's and borg's encryption
DST_POOL_TEMP = "cmp-dst"


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


# --- Disks ---------------------------------------------------------------------------

def free_nbd() -> str:
    for b in sorted(Path("/sys/block").glob("nbd*"), key=lambda p: int(p.name[3:])):
        if not (b / "pid").exists():
            return f"/dev/{b.name}"
    raise SystemExit("no free nbd device")


def attach(image: Path) -> str:
    # The same lock vm-mount and make-from-live.sh take: finding a free device
    # and attaching it must be one step.
    import fcntl
    with open("/run/lock/backotter-nbd.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        dev = free_nbd()
        run(["qemu-nbd", "--connect", dev, "--format", "qcow2", str(image)])
    run(["udevadm", "settle"], quiet=True)
    for _ in range(10):
        if Path(f"{dev}p1").exists():
            break
        time.sleep(0.5)
    return dev


def detach(dev: str) -> None:
    # Disconnecting returns before the qemu-nbd server has exited; the next
    # user of the device would get its late replies, so wait for it.
    pid_file = Path("/sys/block") / Path(dev).name / "pid"
    pid = int(pid_file.read_text()) if pid_file.exists() else None
    run(["qemu-nbd", "--disconnect", dev], check=False, quiet=True)
    while pid is not None and Path(f"/proc/{pid}").exists():
        time.sleep(0.2)


def vm_mount(image: Path, mp: Path, ro: bool) -> str:
    run([str(TESTVM / "vm-mount")] + (["--ro"] if ro else []) + [str(image), str(mp)])
    state = Path("/run/vm-mount") / out(["systemd-escape", "--path", str(mp)])
    return state.read_text().split()[0]          # the nbd device


def vm_umount(mp: Path) -> None:
    run([str(TESTVM / "vm-umount"), str(mp)], check=False)


def blkid(dev: str) -> dict[str, str]:
    r = run(["blkid", "-o", "export", dev], check=False, quiet=True)
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


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


# --- Recreating the layout ----------------------------------------------------------

class Layout:
    """What src looks like: its partitions and how they are mounted."""

    def __init__(self, src_dev: str):
        self.src_dev = src_dev
        self.parts = {}                            # partition number -> blkid fields
        for p in sorted(Path("/dev").glob(Path(src_dev).name + "p*")):
            self.parts[int(p.name.split("p")[-1])] = blkid(str(p))
        self.mounts = mounts_below(SRC_MP)
        self.zfs = None
        for n, fields in self.parts.items():
            if fields.get("TYPE") == "zfs_member":
                self.zfs = self.zfs_layout(n)

    def zfs_layout(self, n: int) -> dict:
        temp = next(m["SOURCE"].split("/")[0] for m in self.mounts if m["FSTYPE"] == "zfs")
        label = out(["zdb", "-l", f"{self.src_dev}p{n}"])
        name = next(line.split("'")[1] for line in label.splitlines() if line.strip().startswith("name:"))
        features = [line.split("\t")[0] for line in out(["zpool", "get", "-H", "-o", "property,value", "all", temp]).splitlines()
                    if line.startswith("feature@") and line.split("\t")[1] in ("enabled", "active")]
        pool_props = {}
        for line in out(["zpool", "get", "-H", "-o", "property,value,source", "ashift,autotrim,compatibility", temp]).splitlines():
            prop, value, source = line.split("\t")
            if source == "local" or prop == "ashift":
                pool_props[prop] = value
        datasets = out(["zfs", "list", "-H", "-o", "name", "-r", temp]).splitlines()
        props: dict[str, dict[str, str]] = {d: {} for d in datasets}
        # -r: without it, zfs get reports only the pool's top dataset.
        for line in out(["zfs", "get", "-r", "-H", "-o", "name,property,value", "-s", "local", "all", temp]).splitlines():
            ds, prop, value = line.split("\t")
            if prop == "mountpoint" and value.startswith(str(SRC_MP)):
                value = "/" + value[len(str(SRC_MP)):].lstrip("/")   # undo the altroot
            props[ds][prop] = value
        return {"partition": n, "name": name, "temp": temp, "features": features,
                "pool_props": pool_props, "datasets": datasets, "props": props}

    def btrfs_subvolumes(self) -> list[str]:
        if not any(f.get("TYPE") == "btrfs" for f in self.parts.values()):
            return []
        listing = out(["chroot", str(SRC_MP), "btrfs", "subvolume", "list", "-a", "/"])
        return [line.split(" path ")[-1].replace("<FS_TREE>/", "") for line in listing.splitlines()]


def in_src(cmd: list[str], root: Path = SRC_MP) -> None:
    """Run a tool from the original system (its mkfs etc.), or from the
    system at root; see src_api()."""
    run(["chroot", str(root)] + cmd)


class src_api:
    """The host's /dev, /proc and /sys bound into the (read-only) src tree,
    or the tree at root, so that tools run there with in_src() see the disks."""

    def __init__(self, root: Path = SRC_MP):
        self.root = root

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

    PARTS = Path("/mnt/cmp-stick-parts")

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


def recreate(layout: Layout, dst_dev: str, tools: Path = SRC_MP) -> None:
    """Lay out dst_dev like src and mount it at DST_MP.  The filesystems are
    made by the mkfs tools of the system at tools: src's own, or the stick's."""
    run(f"dd if={layout.src_dev} of={dst_dev} bs=1M count=1 status=none conv=fsync")
    if out(["blkid", "-p", "-o", "value", "-s", "PTTYPE", layout.src_dev], check=False) == "gpt":
        # The first MiB has GPT's primary header and table; its backup copy
        # belongs in the disk's last sectors, which firmware may check.
        run(["sfdisk", "-q", "--relocate", "gpt-bak-std", dst_dev])
    run(["partprobe", dst_dev], check=False)
    run(["udevadm", "settle"], quiet=True)
    with src_api(tools):
        def mkfs(cmd: list[str]) -> None:
            if run(["chroot", str(tools), "sh", "-c", f"command -v {cmd[0]}"], check=False, quiet=True).returncode:
                raise SystemExit(f"no {cmd[0]} in {tools}" + (" (the stick)" if tools != SRC_MP else ""))
            in_src(cmd, tools)
        for n, f in layout.parts.items():
            dev, fs, uuid, label = f"{dst_dev}p{n}", f.get("TYPE"), f.get("UUID"), f.get("LABEL")
            lab = ["-L", label] if label else []
            if fs in ("ext4", "ext3", "ext2"):
                mkfs([f"mkfs.{fs}", "-q", "-F", "-U", uuid] + lab + [dev])
            elif fs == "vfat":
                # An EFI system partition; FAT's "UUID" is its volume id.
                mkfs(["mkfs.vfat", "-i", uuid.replace("-", "")] + (["-n", label] if label else []) + [dev])
            elif fs == "f2fs":
                mkfs(["mkfs.f2fs", "-q", "-f", "-U", uuid] + (["-l", label] if label else []) + [dev])
            elif fs == "nilfs2":
                mkfs(["mkfs.nilfs2", "-q", "-f"] + lab + [dev])
                mkfs(["nilfs-tune", "-U", uuid, dev])
            elif fs == "xfs":
                mkfs(["mkfs.xfs", "-q", "-f", "-m", f"uuid={uuid}"] + lab + [dev])
            elif fs == "btrfs":
                mkfs(["mkfs.btrfs", "-q", "-f", "-U", uuid] + lab + [dev])
            elif fs == "jfs":
                mkfs(["mkfs.jfs", "-q"] + lab + [dev])
                mkfs(["jfs_tune", "-U", uuid, dev])
            elif fs == "swap":
                mkfs(["mkswap", "-U", uuid] + lab + [dev])
            elif fs == "zfs_member":
                z = layout.zfs
                cmd = ["zpool", "create", "-f", "-d", "-R", str(DST_MP), "-t", DST_POOL_TEMP]
                cmd += [a for feat in z["features"] for a in ("-o", f"{feat}=enabled")]
                cmd += [a for k, v in z["pool_props"].items() for a in ("-o", f"{k}={v}")]
                cmd += [a for k, v in z["props"][z["temp"]].items() for a in ("-O", f"{k}={v}")]
                run(cmd + [z["name"], dev])
            elif fs:
                raise SystemExit(f"don't know how to recreate {fs}")
    with src_api():
        subvols = layout.btrfs_subvolumes()

    # Mount like src: each mount's partition number carries over.
    if layout.zfs:
        z = layout.zfs
        for ds in z["datasets"][1:]:
            props = [a for k, v in z["props"][ds].items() for a in ("-o", f"{k}={v}")]
            run(["zfs", "create"] + props + [DST_POOL_TEMP + ds[len(z["temp"]):]])
        root_ds = next(m["SOURCE"] for m in layout.mounts if Path(m["TARGET"]) == SRC_MP)
        run(["zfs", "mount", DST_POOL_TEMP + root_ds[len(z["temp"]):]], check=False)
    for m in layout.mounts:
        target = DST_MP / os.path.relpath(m["TARGET"], SRC_MP)
        if m["FSTYPE"] == "zfs":
            continue
        n = int(m["SOURCE"].split("[")[0].rsplit("p", 1)[1])
        dev = f"{dst_dev}p{n}"
        if m["FSTYPE"] == "btrfs" and subvols:
            top = Path("/mnt/cmp-btrfs-top")
            top.mkdir(exist_ok=True)
            run(["mount", "-t", "btrfs", "-o", "subvolid=5", dev, str(top)])
            run(["mount", "--bind", str(top), str(SRC_MP / "mnt")])
            with src_api():
                for sv in subvols:
                    # A subvolume inside another (Fedora's root/var/lib/
                    # portables) needs the directories above it; the
                    # restore gives them their metadata.
                    (top / sv).parent.mkdir(parents=True, exist_ok=True)
                    in_src(["btrfs", "subvolume", "create", f"/mnt/{sv}"])
            run(["umount", str(SRC_MP / "mnt")])
            run(["umount", str(top)])
            subvols = []
        opts = [o for o in m["OPTIONS"].split(",") if o.startswith("subvol=")]
        if m["FSTYPE"] == "xfs":
            opts.append("nouuid")    # src, with the same UUID, is mounted too
        target.mkdir(parents=True, exist_ok=True)
        # The type explicitly: right after mkfs, mount can't always probe it.
        run(["mount", "-t", m["FSTYPE"]] + (["-o", ",".join(opts)] if opts else []) + [dev, str(target)])


def unmount_dst(dst_dev: str, layout: Layout) -> None:
    run(["umount", "-R", str(DST_MP)], check=False)
    if layout.zfs:
        run(["zpool", "export", DST_POOL_TEMP], check=False)
    detach(dst_dev)


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
    builds = sorted((WORK_BASE / "rustic" / "dist").glob("rustic-backotter-*-x86_64-unknown-linux-gnu"),
                    key=lambda p: p.stat().st_mtime)
    if not builds:
        raise SystemExit("no rustic-bko build: set RUSTIC_BKO, or run build-backotter.sh")
    return str(builds[-1])


# Back up with one of BackOtter's two programs, restore with the other.
CROSS = {"restic-to-rustic": ("restic-bko", "rustic-bko"),
         "rustic-to-restic": ("rustic-bko", "restic-bko")}


def backup(tool: str, work: Path) -> None:
    repo = work / tool
    tool = CROSS.get(tool, (tool, tool))[0]
    shutil.rmtree(repo, ignore_errors=True)
    env = dict(os.environ, RESTIC_PASSWORD=PASSWORD, RUSTIC_PASSWORD=PASSWORD, BORG_PASSPHRASE=PASSWORD)
    if tool in ("restic", "restic-bko"):
        run([restic_exe(tool), "-r", str(repo), "init", "-q"], env=env)
        run([restic_exe(tool), "-r", str(repo), "backup", "-q", str(SRC_MP)], env=env)
    elif tool == "rustic-bko":
        # The real atime, as restic keeps it (rustic's default is the mtime).
        rustic = [rustic_exe(), "-r", str(repo), "--no-progress", "--no-cache"]
        run(rustic + ["init"], env=env)
        run(rustic + ["backup", "--set-atime", "yes", str(SRC_MP)], env=env)
    elif tool == "borg":
        run(["borg", "init", "-e", "repokey-blake2", str(repo)], env=env)
        run(["borg", "create", "--numeric-ids", "-C", "zstd,3", f"{repo}::backup", "."], env=env, cwd=SRC_MP)
    elif tool == "dar":
        repo.mkdir()
        run(["dar", "-c", str(repo / "backup"), "-R", str(SRC_MP), "-zgzip:6", "-Q", "-q", "-w"])
    elif tool == "bsdtar":
        # pax format, with ACLs, every xattr and the inode flags (bsdtar's
        # "fflags"); it crosses into /boot and the other mounts, as borg does.
        repo.mkdir()
        run(["bsdtar", "-c", "-f", str(repo / "backup.tar.zst"), "--zstd", "--format", "pax",
             "--numeric-owner", "--acls", "--xattrs", "--fflags", "-C", str(SRC_MP), "."])


def restore(tool: str, work: Path) -> None:
    repo = work / tool
    tool = CROSS.get(tool, (tool, tool))[1]
    env = dict(os.environ, RESTIC_PASSWORD=PASSWORD, RUSTIC_PASSWORD=PASSWORD, BORG_PASSPHRASE=PASSWORD)
    if tool in ("restic", "restic-bko"):
        run([restic_exe(tool), "-r", str(repo), "restore", "-q", "--sparse",
             f"latest:{SRC_MP}", "--target", str(DST_MP)], env=env)
    elif tool == "rustic-bko":
        # Numeric ids, as restic restores them: names would be looked up in
        # this system's users, not the guest's.
        run([rustic_exe(), "-r", str(repo), "--no-progress", "--no-cache", "restore", "--numeric-id",
             "--sparse", "by-content", f"latest:{SRC_MP}", str(DST_MP)], env=env)
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


# --- Booting the result -------------------------------------------------------------

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


def firmware_options(guest_dir: Path, layout: dict) -> list[str]:
    """vm-run's options for booting the guest: those its builder left in
    vm-run-options, or else UEFI without Secure Boot (which Pop!_OS doesn't
    support) if it has an EFI system partition (FAT), BIOS if not."""
    f = guest_dir / "vm-run-options"
    if f.exists():
        return shlex.split(f.read_text())
    return ["--uefi", "--no-secure-boot"] if "vfat" in layout.values() else []


# What the guest booted with: bios, uefi, or uefi+secure-boot.
FIRMWARE_PROBE = ("if [ -d /sys/firmware/efi ]; then "
                  "sb=$(od -An -tu1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
                  " 2>/dev/null | awk '{print $NF}'); "
                  "if [ \"$sb\" = 1 ]; then echo uefi+secure-boot; else echo uefi; fi; "
                  "else echo bios; fi")


def boot(image: Path, guest_dir: Path, log: Path, firmware: list[str]) -> dict:
    if not port_free(SSH_PORT):
        raise SystemExit(f"port {SSH_PORT} is taken (a test VM left running?)")
    # The restored disk's qemu-nbd may still be letting go of it (after JFS,
    # for one); vm-run can't open a locked image, and would quietly give up.
    for _ in range(30):
        if run(["qemu-img", "info", str(image)], check=False, quiet=True).returncode == 0:
            break
        time.sleep(1)
    # Its own process group, so that stopping it stops QEMU too, and a VM
    # left running can't hold SSH_PORT against the next boot test.
    vm = subprocess.Popen([str(TESTVM / "vm-run"), "--ram", *firmware, "--serial-log", str(log), str(image)],
                          env=dict(os.environ, SSH_PORT=SSH_PORT), start_new_session=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ssh = ["ssh", "-q", "-i", str(guest_dir / "id_ed25519"), "-p", SSH_PORT, "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "ConnectTimeout=5", "root@127.0.0.1"]
    result = {"ssh": False}
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and vm.poll() is None:
            if run(ssh + ["true"], check=False, quiet=True).returncode == 0:
                result["ssh"] = True
                break
            time.sleep(3)
        if result["ssh"]:
            r = run(ssh + ["timeout 180 systemctl is-system-running --wait; "
                           "systemctl --failed --no-legend --plain | cut -d' ' -f1"],
                    check=False, quiet=True)
            lines = r.stdout.split()
            result["state"] = lines[0] if lines else "?"
            result["failed_units"] = lines[1:]
            result["firmware"] = out(ssh + [FIRMWARE_PROBE], check=False)
            run(ssh + ["poweroff"], check=False, quiet=True)
            vm.wait(timeout=90)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if vm.poll() is None:
            os.killpg(vm.pid, signal.SIGTERM)
            try:
                vm.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(vm.pid, signal.SIGKILL)
                vm.wait()
    return result


# --- One guest ----------------------------------------------------------------------

def work_dir(guest: str, stick_img: Path | None) -> Path:
    return WORK_BASE / "compare" / (guest + (".stick-mkfs" if stick_img else ""))


def compare_guest(guest: str, tools: list[str], stick_img: Path | None = None) -> dict:
    guest_dir = VMS / guest
    base = guest_dir / f"{guest}.qcow2"
    work = work_dir(guest, stick_img)
    work.mkdir(parents=True, exist_ok=True)
    fsmeta = [sys.executable, "-I", str(HERE / "fsmeta.py")]
    results = {"guest": guest, "mkfs": str(stick_img) if stick_img else "guest", "tools": {}}

    print(f"== {guest}: test files", flush=True)
    src = work / "src.qcow2"
    src.unlink(missing_ok=True)
    run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(base), "-F", "qcow2", str(src)])
    vm_mount(src, SRC_MP, ro=False)
    try:
        r = run([str(HERE / "torture.sh"), str(SRC_MP)])
        results["torture_skipped"] = [l for l in r.stderr.splitlines() if "skipped" in l]
    finally:
        vm_umount(SRC_MP)
    # Let the nbd device from that mount be released before attaching again.
    run(["udevadm", "settle"], quiet=True)
    time.sleep(2)

    src_dev = vm_mount(src, SRC_MP, ro=True)
    try:
        layout = Layout(src_dev)
        results["layout"] = {n: f.get("TYPE") for n, f in layout.parts.items()}
        run(fsmeta + ["record", str(SRC_MP), str(work / "before.json")])
        results["source_bytes"] = int(out(["du", "-sbx", str(SRC_MP)]).split()[0]) + sum(
            int(out(["du", "-sbx", m["TARGET"]]).split()[0]) for m in layout.mounts[1:])
        for tool in tools:
            print(f"== {guest}: {tool} backup", flush=True)
            t = time.monotonic()
            backup(tool, work)
            results["tools"][tool] = {"backup_s": round(time.monotonic() - t, 1),
                                      "backup_bytes": size(work / tool)}
        for tool in tools:
            print(f"== {guest}: {tool} restore", flush=True)
            res = results["tools"][tool]
            dst = work / f"restored-{tool}.qcow2"
            dst.unlink(missing_ok=True)
            run(["qemu-img", "create", "-q", "-f", "qcow2", str(dst), "16G"])
            dst_dev = attach(dst)
            try:
                if stick_img:
                    with stick_root(stick_img) as stick:
                        recreate(layout, dst_dev, stick)
                else:
                    recreate(layout, dst_dev)
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
                run(fsmeta + ["record", str(DST_MP), str(work / f"after-{tool}.json")])
                r = run(fsmeta + ["compare", str(work / "before.json"), str(work / f"after-{tool}.json"),
                                  "--json", str(work / f"diff-{tool}.json")], check=False)
                print(r.stdout, flush=True)
                res["diff"] = json.loads((work / f"diff-{tool}.json").read_text())["differences"]
            finally:
                unmount_dst(dst_dev, layout)
            print(f"== {guest}: booting the {tool} restore", flush=True)
            res["boot"] = boot(dst, guest_dir, work / f"boot-{tool}.log",
                               firmware_options(guest_dir, results["layout"]))
            print(f"   {res['boot']}", flush=True)
        results["coverage"] = json.loads((work / f"diff-{tools[0]}.json").read_text())["coverage"]
    finally:
        vm_umount(SRC_MP)
    # Keep an earlier run's results for the tools this run left out, so one
    # tool can be rerun alone.  (torture.sh makes the same files each time.)
    old = work / "results.json"
    if old.exists():
        earlier = json.loads(old.read_text()).get("tools", {})
        results["tools"] = {t: v for t, v in earlier.items() if t not in tools} | results["tools"]
    old.write_text(json.dumps(results, indent=1))
    return results


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    args = sys.argv[1:]
    boot_only, stick_img = False, None
    while args and args[0].startswith("--"):
        if args[0] == "--boot-only":
            boot_only, args = True, args[1:]
        elif args[0] == "--mkfs-from" and len(args) > 1:
            stick_img, args = Path(args[1]).resolve(), args[2:]
            if not stick_img.is_file():
                raise SystemExit(f"no such image: {stick_img}")
        else:
            raise SystemExit(__doc__)
    if not args:
        raise SystemExit(__doc__)
    guest, tools = args[0], args[1:] or ["restic", "borg", "dar", "bsdtar"]
    for m in (SRC_MP, DST_MP, STICK_MP):
        m.mkdir(parents=True, exist_ok=True)
    if boot_only:
        # Boot the disks a previous run restored, and update its results.
        work = work_dir(guest, stick_img)
        r = json.loads((work / "results.json").read_text())
        for tool in tools:
            print(f"== {guest}: booting the {tool} restore", flush=True)
            r["tools"][tool]["boot"] = boot(work / f"restored-{tool}.qcow2", VMS / guest,
                                            work / f"boot-{tool}.log", firmware_options(VMS / guest, r["layout"]))
            print(f"   {r['tools'][tool]['boot']}", flush=True)
        (work / "results.json").write_text(json.dumps(r, indent=1))
    else:
        r = compare_guest(guest, tools, stick_img)
    print(f"\n{'tool':16} {'backup':>8} {'size':>9} {'restore':>8}  boot      differences")
    for tool, t in r["tools"].items():
        diffs = ", ".join(f"{k} {v['count']}" for k, v in t.get("diff", {}).items()) or "none"
        b = t.get("boot", {})
        print(f"{tool:16} {t['backup_s']:7}s {t['backup_bytes'] / 2**20:7.0f}MB {t.get('restore_s', 0):7}s  "
              f"{b.get('state', 'no ssh'):9} {diffs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
