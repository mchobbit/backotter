#!/usr/bin/env bash
# rebuild-one.sh GUEST [DISK MEM SLOT]: build GUEST from scratch with today's
# builder into REBUILD_DIR/vms/GUEST, then boot it once and say how it came
# up. The existing ~/backotter-work/vms/GUEST is only read (its ISOs and
# keys, linked or copied in). Run as root by cmp-run.sh, in a systemd scope,
# from a fixed copy of testvm/, so edits to the checkout don't reach a
# rebuild under way:
#   rm -rf /bkoscratch/rebuild/testvm; mkdir -p /bkoscratch/rebuild
#   cp -a ~/backotter-work/testvm /bkoscratch/rebuild/testvm
#   WEAR_TAG=rebuild:TAG CMP_CMD=/bkoscratch/rebuild/testvm/rebuild-one.sh ~/backotter-work/cmp-run.sh TAG SLOTS GUEST=DISK%MEM ...
# REBUILD_DIR (default /bkoscratch/rebuild) holds that testvm/ and vms/;
# sudo drops it from the environment, so another one goes in CMP_CMD:
# CMP_CMD="env REBUILD_DIR=DIR DIR/testvm/rebuild-one.sh".
# Builders that don't need root run as m, with TMPDIR=/mnt/bko-ram for their
# small scratch; the root ones (make-from-live.sh, make-debian-zfs.sh,
# make-debian-stack.sh, make-chimera-zfs.sh) without a TMPDIR, so in /tmp: theirs would reach the
# guest's chroot. Images go to REBUILD_DIR. Results: REBUILD_DIR/vms/GUEST/rebuild-build.json and
# rebuild-boot.json; exit 0 only if it built and booted.
set -u
G=$1 SLOT=${4:-1}
R=${REBUILD_DIR:-/bkoscratch/rebuild} HOME_M=/home/m
T=$R/testvm W=$R/vms/$G
# The copy in R, never the checkout's (whose vms/ is the guests' own).
[ "$(readlink -f "$0")" = "$(readlink -f $T/rebuild-one.sh)" ] ||
    { echo "run $T/rebuild-one.sh, or set REBUILD_DIR to this copy's" >&2; exit 2; }
# TMPDIR=/mnt/bko-ram only for the builders run as m: the root ones chroot
# into the guest, where that path doesn't exist.
RAMTMP=/mnt/bko-ram; unset TMPDIR
export COMPARE_SLOT=$SLOT SUDO_USER=m
# Inputs: a host without the ISOs gets copies in /bkoscratch/rebuild-inputs.
SRC=$HOME_M/backotter-work/vms/$G
[ -d /bkoscratch/rebuild-inputs/$G ] && SRC=/bkoscratch/rebuild-inputs/$G
# elementary8-luks is built from elementary8's ISO.
[ $G = elementary8-luks ] && [ ! -d /bkoscratch/rebuild-inputs/$G ] && [ -d /bkoscratch/rebuild-inputs/elementary8 ] &&
    SRC=/bkoscratch/rebuild-inputs/elementary8
[ -d $T ] || { echo "no $T" >&2; exit 2; }
[ -d "$SRC" ] || { echo "no $SRC" >&2; exit 2; }
asm() { runuser -u m -- env TMPDIR="$RAMTMP" PATH="$PATH:/usr/sbin:/sbin" "$@"; }
note=()

case $G in
debian12)   cmd=(asm $T/make-debian.sh 12.0.0 ext4 "$W") ;;
debian13)   cmd=(asm $T/make-debian.sh 13.0.0 btrfs "$W") ;;
devuan6)    cmd=(asm $T/make-debian.sh --devuan --init openrc 6.1.1 ext3 "$W") ;;
# kali: from Kali's netinst ISO, SHA256SUMS(.gpg) and kali-signing-key.gpg in
# /bkoscratch/rebuild-inputs/kali (linked and copied in below, so nothing is
# downloaded); kali-rolling's packages as they are at the rebuild.
kali)       cmd=(asm $T/make-debian.sh --kali 2026.3 ext4 "$W") ;;
debian13-zfs) cmd=($T/make-debian-zfs.sh "$W" debian13-zfs) ;;
debian13-stack) cmd=($T/make-debian-stack.sh "$W" debian13-stack) ;;
ubuntu24)   cmd=(asm $T/make-ubuntu.sh 24.04 "$W") ;;
ubuntu24-uefi) cmd=(asm $T/make-ubuntu.sh --uefi --name ubuntu24-uefi 24.04.5 "$W") ;;
ubuntu24-luks) cmd=(asm $T/make-ubuntu.sh --uefi --luks --name ubuntu24-luks 24.04.5 "$W") ;;
ubuntu26-uefi) cmd=(asm $T/make-ubuntu.sh --uefi --name ubuntu26-uefi 26.04.1 "$W") ;;
ubuntu26-zfs) cmd=(asm $T/make-ubuntu.sh --uefi --zfs-guided --name ubuntu26-zfs 26.04.1 "$W") ;;
ubuntu26-zfsmirror) cmd=(asm $T/make-ubuntu.sh --zfs-mirror --name ubuntu26-zfsmirror 26.04.1 "$W") ;;
ubuntu26-raid) cmd=(asm $T/make-ubuntu.sh --uefi --raid --name ubuntu26-raid 26.04.1 "$W") ;;
ubuntu26-luks) cmd=(asm $T/make-ubuntu.sh --uefi --luks --name ubuntu26-luks 26.04.1 "$W") ;;
fedora44)   cmd=(asm $T/make-fedora.sh 44-1.7 "$W") ;;
fedora44-uefi) cmd=(asm $T/make-fedora.sh --uefi --name fedora44-uefi 44-1.7 "$W") ;;
fedora44-raid) cmd=(asm $T/make-fedora.sh --uefi --raid --name fedora44-raid 44-1.7 "$W") ;;
# kinoite44: the :44 tag as it is at the rebuild, so newer content than the
# guest's (kinoite44.digest says which).
kinoite44)  cmd=(asm $T/make-fedora.sh --ostree quay.io/fedora/fedora-kinoite:44 --name kinoite44 44-1.7 "$W") ;;
rocky10)    cmd=(asm $T/make-rocky.sh 10.0 "$W") ;;
rocky10-lvm) cmd=(asm $T/make-rocky.sh --lvm 10.0 "$W") ;;
rocky10-raid5) cmd=(asm $T/make-rocky.sh --raid5 10.0 "$W") ;;
tumbleweed) cmd=(asm $T/make-opensuse.sh 20261030 "$W") ;;
alpine324)  cmd=(asm $T/make-bootstrap.sh alpine "$W") ;;
void-musl)  cmd=(asm $T/make-bootstrap.sh void "$W") ;;
chimera)    cmd=(asm $T/make-bootstrap.sh chimera "$W") ;;
chimera-uefi) cmd=(asm $T/make-bootstrap.sh chimera-uefi "$W") ;;
chimera-zfs) cmd=($T/make-chimera-zfs.sh "$W" chimera-zfs) ;;
# gentoo: its stage3 at the pinned date, from /bkoscratch/rebuild-inputs/gentoo
# (Gentoo prunes autobuilds); the tree and packages as they are at the rebuild.
gentoo)     cmd=(asm $T/make-bootstrap.sh gentoo "$W") ;;
# gentoo-systemd: the same, with the systemd stage3 from
# /bkoscratch/rebuild-inputs/gentoo-systemd.
gentoo-systemd) cmd=(asm $T/make-bootstrap.sh gentoo-systemd "$W") ;;
artix-dinit|artix-s6) cmd=(asm $T/make-bootstrap.sh $G "$W") ;;
nixos2605)  cmd=(asm $T/make-bootstrap.sh nixos "$W") ;;
arch)       cmd=(asm $T/make-arch.sh 2026.10.01 "$W") ;;
cachyos)    cmd=(asm $T/make-arch.sh --cachyos --root-fs jfs 2026.10.01 "$W") ;;
# cachyos-zfs: from CachyOS's live ISO in /bkoscratch/rebuild-inputs/cachyos-zfs
# (or the guest's own dir); CachyOS's and Arch's packages as they are at the rebuild.
cachyos-zfs) cmd=(asm $T/make-cachyos-zfs.sh --iso-dir "$SRC" 260809 "$W") ;;
# pikaos4: from PikaOS's live ISO in /bkoscratch/rebuild-inputs/pikaos4 (or the
# guest's own dir), its SHA-256 pinned in the builder; the ISO's packages only.
pikaos4)    cmd=(asm $T/make-pikaos.sh --iso-dir "$SRC" pikaos4 "$W") ;;
# pikaos4-luks: from pikaos4's ISO (Btrfs on LUKS2, BOOTX64 mode); a new
# console-passphrase each build.
pikaos4-luks) cmd=(asm $T/make-pikaos.sh --iso-dir /bkoscratch/rebuild-inputs/pikaos4 pikaos4-luks "$W") ;;
# proxmox9, proxmox9-mirror: Proxmox's automated installer, from the ISO, its
# .asc, the release key and the assistant's .deb in /bkoscratch/rebuild-inputs/proxmox9
# (all pinned in the builder; the prepared ISO is made there per first-boot.sh).
proxmox9|proxmox9-mirror) cmd=(asm $T/make-proxmox.sh --iso-dir /bkoscratch/rebuild-inputs/proxmox9 $G "$W") ;;
# vanilla3: from Vanilla OS's live ISO in /bkoscratch/rebuild-inputs/vanilla3 (or
# the guest's own dir), its SHA-256 pinned in the builder; the system image
# ghcr.io/vanilla-os/gnome:latest as it is at the rebuild (pulled by albius;
# the builder's abroot upgrade --force fills root B from it). ~22 GiB at its peak.
vanilla3)   cmd=(asm $T/make-vanilla.sh --iso-dir "$SRC" vanilla3 "$W") ;;
# anduinos2: from AnduinOS's live ISO in /bkoscratch/rebuild-inputs/anduinos2 (or
# the guest's own dir), its SHA-256 pinned in the builder; the ISO's packages only.
anduinos2)  cmd=(asm $T/make-anduinos.sh "$SRC/AnduinOS-2.0.5-amd64.iso" "$W" anduinos2) ;;
# guix15, guix15-bios: from Guix's 1.5.0 installation ISO in
# /bkoscratch/rebuild-inputs/guix15 (its SHA-256 pinned in the builder, its
# .sig checked on download), with 1.5.0's substitutes at the rebuild.
guix15|guix15-bios) cmd=(asm $T/make-guix.sh --iso-dir /bkoscratch/rebuild-inputs/guix15 $G "$W") ;;
endeavouros) cmd=(asm $T/make-arch.sh --live endeavouros "$SRC/EndeavourOS_Titan-Nova-2026.08.15.iso" 2026.10.01 "$W") ;;
endeavouros-grub) cmd=(asm $T/make-arch.sh --live endeavouros-grub /bkoscratch/rebuild-inputs/endeavouros/EndeavourOS_Titan-Nova-2026.08.15.iso 2026.10.01 "$W") ;;
manjaro)    cmd=(asm $T/make-arch.sh --live manjaro "$SRC/manjaro-xfce-26.1.1-minimal-260825-linux71.iso" 2026.10.01 "$W") ;;
# garuda: from Garuda's dr460nized live ISO in /bkoscratch/rebuild-inputs/garuda,
# its SHA-256 pinned in make-arch.sh; Garuda's, chaotic-aur's and Arch's packages
# as on the ISO.
garuda)     cmd=(asm $T/make-arch.sh --live garuda "$SRC/garuda-dr460nized-linux-garuda-260819.iso" 2026.10.01 "$W") ;;
# biglinux: from BigLinux's k618 live ISO in /bkoscratch/rebuild-inputs/biglinux,
# SHA-256 1d12186abab0a2cb33ee9f0edd30140cd8ed842aa081e90ad4f0e3d050e6ae90, pinned
# in make-arch.sh (BigLinux signs no ISO); its rootfs and desktopfs as on the ISO.
biglinux)   cmd=(asm $T/make-arch.sh --live biglinux "$SRC/biglinux_2026-10-03_k618.iso" 2026.10.01 "$W") ;;
mx25)       cmd=($T/make-from-live.sh mx ext2 "$SRC/MX-25.3_Xfce_x64.iso" "$W" mx25) ;;
mint22)     cmd=($T/make-from-live.sh mint f2fs "$SRC/linuxmint-22.3-xfce-64bit.iso" "$W" mint22) ;;
pop24)      cmd=($T/make-from-live.sh pop ext4 "$SRC/pop-os_24.04_amd64_intel_20.iso" "$W" pop24) ;;
zorin18)    cmd=($T/make-from-live.sh zorin nilfs2 "$SRC/Zorin-OS-18.1-Core-64-bit.iso" "$W" zorin18) ;;
neon24)     cmd=($T/make-from-live.sh neon ext4 "$SRC/neon-user-desk-20261025-1825.iso" "$W" neon24) ;;
elementary8) cmd=($T/make-from-live.sh elementary ext4 "$SRC/elementaryos-8.1-stable-amd64.20260219.iso" "$W" elementary8) ;;
elementary8-luks) cmd=($T/make-from-live.sh --luks elementary ext4 "$SRC/elementaryos-8.1-stable-amd64.20260219.iso" "$W" elementary8-luks) ;;
omarchy)    cmd=(asm $T/make-omarchy.sh 4.0.6 "$W") ;;
freebsd15)  cmd=(asm $T/make-freebsd.sh 15.1 zfs "$W") ;;
freebsd15-ufs) cmd=(asm $T/make-freebsd.sh 15.1 ufs "$W") ;;
freebsd15-geli) cmd=(asm $T/make-freebsd.sh --uefi-geli 15.1 zfs "$W") ;;
freebsd14)  cmd=(asm $T/make-freebsd.sh 14.5 zfs "$W") ;;
hardenedbsd15) cmd=(asm $T/make-hardenedbsd.sh 15 23 ufs "$W") ;;
# ghostbsd26: its install VM takes 8 GB (INSTALL_MEMORY): reserve ~9 GiB.
ghostbsd26) cmd=(asm $T/make-ghostbsd.sh 26.2-R15.1p6 ufs "$W") ;;
ghostbsd26-zfs) cmd=(asm $T/make-ghostbsd.sh 26.2-R15.1p6 zfs "$W") ;;
aerynos)    cmd=(asm $T/make-aerynos.sh 2026.11 2026-11-02-unstable-stream "$W") ;;
openbsd8)   cmd=(asm $T/make-openbsd.sh 8.0 "$W") ;;
netbsd11)   cmd=(asm $T/make-netbsd.sh 11.1 "$W") ;;
alpinebsd)  cmd=(asm $T/make-netbsd.sh --alpinebsd 11.0 "$W") ;;
dragonfly6) cmd=(asm $T/make-dragonfly.sh 6.4.2 "$W") ;;
omnios58)   cmd=(asm $T/make-omnios.sh r151058 "$W") ;;
# oi2610: OpenIndiana's text ISO and its installer, driven over the console;
# oi2610-bios: OpenIndiana's supplemental cloud image. Both in
# /bkoscratch/rebuild-inputs/GUEST, their SHA-256s pinned in make-openindiana.sh
# (OpenIndiana signs neither); the packages as on the media (no pkg update).
oi2610)     cmd=(asm $T/make-openindiana.sh oi2610 "$SRC/OI-hipster-text-20261030.iso" "$W") ;;
oi2610-bios) cmd=(asm $T/make-openindiana.sh --cloud oi2610-bios "$SRC/OI-hipster-cloudimage-20261107.img.zst" "$W") ;;
sculpt2610) cmd=(asm $T/make-sculpt.sh 26.10 "$W") ;;
*) echo "no recipe for $G" >&2; exit 2 ;;
esac
# The image's name: make-freebsd.sh calls it freebsd$major.qcow2 whatever the
# guest, make-ghostbsd.sh ghostbsd$major.qcow2.
IMG=$G; case $G in freebsd15-ufs|freebsd15-geli) IMG=freebsd15 ;; ghostbsd26-zfs) IMG=ghostbsd26 ;; esac
# Never two VMs on one port: compare.py's boot uses 2230 + COMPARE_SLOT.
if ss -ltnH "sport = :$((2230 + SLOT))" | grep -q .; then echo "port $((2230 + SLOT)) is taken" >&2; exit 2; fi
# The ISO: the recipe's .iso argument, wherever options put it.
case $G in mx25|mint22|pop24|zorin18|neon24|elementary8|elementary8-luks|anduinos2)
    ISO=""; for a in "${cmd[@]}"; do case $a in *.iso) ISO=$a ;; esac; done
    [ -f "$ISO" ] || { echo "no ISO $ISO" >&2; exit 2; } ;;
proxmox9|proxmox9-mirror)
    ISO=/bkoscratch/rebuild-inputs/proxmox9/proxmox-ve_9.2-1.iso
    [ -f "$ISO" ] || { echo "no ISO $ISO" >&2; exit 2; } ;;
esac
leftovers() {  # what a failed root builder may leave: this build's loop devices; mounts, pools, arrays, VGs and LUKS volumes of any build on the host
    { losetup -a | grep -F "$W"; findmnt -rno TARGET | grep -E '^/mnt/(bko-debian-zfs|bko-debian-stack|bko-chimera-zfs|live-)'
      zpool list -H -o name 2>/dev/null | grep -E '^bko(dz|cz)'
      # make-from-live.sh --layout distinst: VG "data" on a loop device (or in cryptdata), LUKS volume "cryptdata".
      pvs --devicesfile "" --noheadings -o vg_name,pv_name 2>/dev/null | awk '$1 == "data" && $2 ~ /^\/dev\/(loop|mapper\/cryptdata$)/ { print "vg-data:" $2 }'
      ls /dev/md 2>/dev/null | grep '^bkos'; dmsetup ls 2>/dev/null | awk '$1 ~ /^bkos/ || $1 == "cryptdata" { print $1 }'; } | tr '\n' ' '
}

mkdir -p $R/vms && chown m:m $R $R/vms
if [ -e $W/$IMG.qcow2 ] && [ "$(cat $W/build.exit 2>/dev/null)" = 0 ]; then
    echo "== $G: already built; booting it again"
else
    # A fresh WORK_DIR: a failed attempt's leftovers would stop the builder.
    if [ -e $W ]; then mv $W $W.failed-$(date +%s); fi
    install -d -o m -g m $W
    # The inputs: everything in SRC but the guest's own outputs. Big files
    # are linked (only read), small ones copied (some builders rewrite them).
    for f in "$SRC"/*; do
        [ -f "$f" ] || continue
        [ -e "$f.part" ] && continue        # an unfinished download: the builder fetches it
        b=${f##*/}
        case $b in
        *.qcow2|root-password|id_ed25519*|*.log|*.log.*|*.exit|*.start|image-state|vm-run-options|\
        *-passphrase|*.verified|*.png|*.json|*.part|*.raw|update*|vmupdate*|install-media.*|*.try1|rebuild*) continue ;;
        esac
        if [ "$(stat -Lc %s "$f")" -gt $((64 << 20)) ]; then ln -s "$(readlink -f "$f")" $W/$b
        else cp -L "$f" $W/$b; fi
    done
    # Debian's builder reads debian-signing-key.gpg, the old one debian-cd-key.gpg.
    [ -e $W/debian-cd-key.gpg ] && [ ! -e $W/debian-signing-key.gpg ] && cp $W/debian-cd-key.gpg $W/debian-signing-key.gpg
    chown -h m:m $W/* 2>/dev/null
    for l in $W/*; do [ -L "$l" ] && [ ! -e "$l" ] && { echo "$l: target gone" >&2; exit 2; }; done
    echo "== $G: building: ${cmd[*]}"
    t0=$(date +%s)
    (cd $W && "${cmd[@]}") >$W/build.log 2>&1
    rc=$?
    echo $rc >$W/build.exit; chown m:m $W/build.log $W/build.exit
    echo "== $G: builder exit $rc after $(( $(date +%s) - t0 )) s"
    tail -5 $W/build.log
    left=""; [ $rc = 0 ] || left=$(leftovers)
    printf '{"guest": "%s", "build_exit": %d, "build_s": %d, "leftovers": "%s"}\n' \
        $G $rc $(( $(date +%s) - t0 )) "$left" >$W/rebuild-build.json
    [ -n "$left" ] && echo "== $G: left behind: $left"
    [ $rc = 0 ] && [ -e $W/$IMG.qcow2 ] || { echo "== $G: build failed"; exit 1; }
fi

# Hardware the builder should have recorded: if it didn't and the old guest
# has some, borrow it for the boot, and say so (a finding about the builder).
if [ ! -e $W/vm-run-options ] && [ -e $HOME_M/backotter-work/vms/$G/vm-run-options ]; then
    cp $HOME_M/backotter-work/vms/$G/vm-run-options $W/vm-run-options.borrowed
fi
[ -e $W/vm-run-options.borrowed ] && [ ! -e $W/vm-run-options ] &&
    note+=("vm-run-options not written by the builder; borrowed the old guest's: $(cat $W/vm-run-options.borrowed)")

echo "== $G: booting it"
rm -f $W/rebuild-boot.json $W/vm-run.*.qcow2 $W/rebuild-boot-console.log.monitor
if [ $G = sculpt2610 ]; then
    asm $T/sculpt-check.sh $W >$W/rebuild-boot.log 2>&1; brc=$?
    printf '{"guest": "%s", "boot": {"sculpt_check_exit": %d}}\n' $G $brc >$W/rebuild-boot.json
else
    python3 -I - "$T/compare" "$W" "$IMG" "${note[@]+"${note[@]}"}" >$W/rebuild-boot.log 2>&1 <<'EOF'
import json, shlex, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
w = Path(sys.argv[2]); notes = sys.argv[4:]
try:
    import compare as c     # in the try: it exits at import on a bad COMPARE_SLOT
    # The BSDs' and OmniOS's way to power off, where Linux's poweroff isn't one.
    ssh = c.VM.ssh
    # (In sh: DragonFly root's shell is csh.)
    OFF = "sync; sh -c 'PATH=/sbin:/usr/sbin:$PATH; poweroff || halt -p || shutdown -p now || shutdown -y -i5 -g0'"
    c.VM.ssh = lambda self, cmd, *a, **k: ssh(self, OFF if cmd == "sync; poweroff" else cmd, *a, **k)
    cfg = c.GuestConfig(w)
    cfg.base = w / f"{sys.argv[3]}.qcow2"
    borrowed = w / "vm-run-options.borrowed"
    if borrowed.exists() and cfg.options is None:   # in memory only: W keeps what the builder wrote
        opts, cfg.options = shlex.split(borrowed.read_text()), []
        while opts:
            o = opts.pop(0)
            if o == "--disk" and opts:
                cfg.disks.append(w / opts.pop(0))
            else:
                cfg.options.append(o)
    b = c.boot([cfg.base] + cfg.disks, cfg, cfg.hardware({}), w / "rebuild-boot-console.log")
except BaseException as e:
    b = {"ssh": False, "error": repr(e)}
json.dump({"guest": w.name, "boot": b, "notes": notes}, open(w / "rebuild-boot.json", "w"), indent=1)
print(json.dumps(b))
sys.exit(0 if b.get("ssh") else 1)
EOF
    brc=$?
fi
chown m:m $W/rebuild-boot* 2>/dev/null
tail -3 $W/rebuild-boot.log
echo "== $G: boot check exit $brc"
exit $brc
