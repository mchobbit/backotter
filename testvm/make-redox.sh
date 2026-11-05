#!/usr/bin/env bash
# Build a Redox OS test guest for BackOtter with redox_installer, under QEMU
# with KVM.  No unattended installer runs inside Redox (its TUI and GUI
# installers are interactive), but upstream's own images are made by
# redox_installer on a Linux host, and so is this one: it partitions a disk
# file, makes RedoxFS on it, mounts that with FUSE and installs Redox's
# signed binary packages into it from static.redox-os.org, as configured by
# upstream's server.toml (with minimal.toml and base.toml) and redox/bko.toml
# on top.  This then checks what was installed (redoxfs, from Linux), adds a
# swap partition after RedoxFS, boots the guest once, checks it on its
# serial console and powers it off from there.  A serial-console guest, like
# sculpt2610: Redox's sshd starts and listens, but serves no logins yet.
#
# Usage: ./make-redox.sh RELEASE WORK_DIR
#   e.g. ./make-redox.sh 0.9.0 vms/redox
#
# RELEASE is the VERSION_ID that upstream's config/base.toml declares
# (every daily build says 0.9.0): this dies once upstream has moved on, so
# that a new version is a new, deliberate build.  The configs come fresh
# from the newest commit of gitlab.redox-os.org/redox-os/redox every build,
# to match the packages, which are rolling (only the newest build of each
# is kept); over HTTPS, trusted on first use.  The packages are checked by
# pkg-lib against Redox's package key, pinned below (the installer fetches
# it itself, so the pin catches a changed key, not an attack on that fetch).
# Results, in WORK_DIR:
#   redox.qcow2           the guest's 16 GiB disk (sparse), with a qcow2
#                         snapshot "fresh-install" after its first boot
#   root-password         root's password (and "user"'s), for the serial
#                         console's login
#   id_ed25519, .pub      SSH key for root, for when sshd works
#   no-ssh                why SSH doesn't serve logins (what sshd logged);
#                         only while it doesn't
#   install.log           redox_installer's output
#   first-boot-serial.log the guest's first boot: kernel, init, a login and
#                         sshd's complaints
#   first-boot.log        what the first boot's checks saw on the console
#                         (uname, the host name, sh -s, pkg and the power
#                         commands, df, os-release, the package index's build)
#   vm-run-options        "--q35 --no-vga --disk-bus sata --nic e1000", how
#                         vm-run must boot it
#   redox-packages.toml   what was installed: the image's /etc/pkg/packages.toml
#                         (each package's blake3), read before the first boot
#   redox-repo.toml       the package index those come from (its build_id
#                         names the daily build): checked against the above
#   redox-config.commit, redox-config/{base,minimal,server}.toml,
#   redox-fetched.sha256  upstream's commit and configs as fetched, and the
#                         sha256 of each fetched file (the installer gets
#                         base.toml with its prompt = false turned on)
#   redox-tools           the redox_installer and redoxfs that built it
# The records are rewritten by every build, never reused.
#
# The guest, host name redox: GPT with 1, "BIOS" (LBA 34 to 1 MiB, the BIOS
# boot loader's stage 2), 2, "EFI" (1 MiB, FAT: the UEFI boot loader), 3,
# "REDOX", RedoxFS from 2 MiB to 14 GiB (/; no /etc/fstab: the boot loader
# passes its UUID to init), and 4, "swap", 2 GiB of Linux swap at the end.
# Partition 3's GPT type is the Linux file system's (0FC63DAF-...), as
# redox_installer writes it on every Redox disk (there is no RedoxFS type):
# blkid, and BackOtter, see a Linux data partition holding no file system
# they know.  Kept, so that the guest looks like a real Redox install; it
# boots as well with another type (tried, 2026-11), as the boot loader
# finds RedoxFS by its offset and init by its UUID.
# Redox's deviations from the test-guest spec, none avoidable today:
#   - two small boot partitions, not one: the installer's fixed layout, and
#     the BIOS boot loader looks for RedoxFS at 2 MiB exactly (so nothing
#     may go before partition 3);
#   - the swap is nominal: Redox can't page to disk (no swap support), so
#     partition 4 is a real, mkswap'ed swap partition that Redox never
#     touches, there for BackOtter to handle beside a file system it can't
#     read;
#   - / is RedoxFS, not a randomly chosen file system: the boot loader and
#     init root from nothing else;
#   - no SSH (below).
# Upstream's server set: no GUI; bash (also sh), coreutils, curl, git, pkg;
# plus OpenSSH's sshd, started by init (redox/bko.toml).  Root's home is
# /home/root.  2 GB RAM, DHCP (10.0.2.15 as the fallback), IPv4
# only.  BIOS (SeaBIOS) on q35, and no graphics card: with video modes to
# offer, the boot loader waits at its mode menu for a key on the PS/2
# keyboard, which no serial console sends.  The disk is on AHCI and the NIC
# an e1000, as upstream runs it.  poweroff and reboot are small scripts that
# run Redox's shutdown; /sbin is a link to /usr/bin.
#
# The way in is the serial console: root, with root-password.  To use it,
#   vm-run $(<WORK_DIR/vm-run-options) WORK_DIR/redox.qcow2
# (vm-run's -nographic: the console on the terminal), or, from a script, the
# console on a socket and redox/console.py, as the first boot below does:
#   vm-run $(<WORK_DIR/vm-run-options) WORK_DIR/redox.qcow2 -- \
#       -chardev socket,id=con,path=SOCK,server=on,wait=off -serial chardev:con -monitor none
#   redox/console.py SOCK WORK_DIR/root-password run 'uname -a'
# SSH doesn't serve logins (2026-11): sshd stays installed, with root's key,
# but isn't started at boot (it would keep a host CPU busy), so that once
# upstream fixes it, starting it (/usr/bin/sshd) reaches the guest with
#   ssh -i WORK_DIR/id_ed25519 -p 2222 root@127.0.0.1
# once upstream fixes it, but today a login gets no session.  relibc's
# chroot (always EPERM), setgroups and initgroups (ENOSYS) and setitimer
# (ENOSYS) are stubs, and each is fatal to sshd's privilege separation or
# its login timer; past them (the calls renamed in the binaries, as a
# test), the session still stalls before its channel opens, as root or as
# an ordinary user.  The first boot starts sshd from the console, checks
# that it listens, tries a login, and records what sshd logged in no-ssh.  So vm-update, compare.py,
# rebuild-one.sh and SticTest1, which all need SSH, can't use this guest
# yet (don't pass it to them); update/in-guest.sh has its pkg case ready.
# Also: the server set's grep panics at every run (2026-11: "RELIBC PANIC
# ... failed to initialize FILETABLE", a build against an older relibc), so
# the guest gets uutils-grep's, which the first boot checks; root's chown -R
# silently changes nothing, and su has no -c.
# vm-mount can't open it: Linux has no RedoxFS driver.  redoxfs (FUSE, from
# the same cargo install as redox_installer) reads a throwaway raw copy,
# never the image: it opens what it mounts read-write.
# No secret goes through the installer's config: it writes the whole merged
# config to /filesystem.toml, mode 0644 (for its TUI installer, which
# nothing here runs), before the packages, and an overwrite later leaves
# those blocks, in the clear, in RedoxFS's free space (a first version
# leaked root's password and the host key so).  So redox/answer-passwords.py
# types the password at the installer's own prompts, this writes sshd's
# private host key in through redoxfs after the install, and bko.toml still
# overwrites /filesystem.toml with a note, mode 0600.  Before the first boot,
# every block of the disk that holds data, free or not, is checked: the
# password nowhere, the host key once.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, sfdisk, mkswap, curl,
# python3 (3.11 or newer), ssh, openssl, fusermount3, and redox_installer
# 0.2.42 and redoxfs 0.9.1, installed once per host with
#   cargo +1.94.1 install --locked --version 0.2.42 --root ~/backotter-work/tools/redox redox_installer
#   cargo +1.94.1 install --locked --version 0.9.1 --root ~/backotter-work/tools/redox redoxfs
# (REDOX_INSTALLER and REDOXFS override where they are).  The installer
# works in /tmp (redox_installer_PID, pkg_download), so one build per host
# at a time.  Runs as an ordinary user in the kvm group.  SSH_PORT picks the
# first boot's port (default: a free one).

set -euo pipefail

DISK_SIZE=16G
INSTALL_SIZE=14G                # RedoxFS's disk file before the swap is added
SWAP_SIZE=2G
MEMORY=2048                     # vm-run's default, which this keeps
INSTALL_TIMEOUT=1800            # seconds for redox_installer (a few hundred MB of packages)
INSTALL_TRIES=3                 # its downloads time out now and then, and it doesn't retry
FIRST_BOOT_TIMEOUT=900          # seconds, until it has powered off
LOGIN_TIMEOUT=300               # seconds from QEMU's start to root's shell on the console
POWEROFF_TIMEOUT=120
# What Redox's sshd says when a login gets as far as it can there.
SSHD_FATAL='chroot\("/var/empty"\): Operation not permitted|login grace time (setitimer|clear) failed|(setgroups|initgroups).*Function not implemented'
INSTALLER_VERSION=0.2.42
REDOXFS_VERSION=0.9.1
REPO_KEY=578b09da56eb4ae1d1ab356b84ab860ca763ca859bed7557a91954c3bf59677e   # static.redox-os.org/pkg/id_ed25519.pub.toml, 2026-11-04
ROOT_PART=3 SWAP_PART=4
REDOXFS_START=4096              # sectors: the BIOS boot loader's fixed 2 MiB
SWAP_TYPE=0657FD6D-A4AB-43C4-84E5-0933C84B4F4F
REDOX_GIT=https://gitlab.redox-os.org/redox-os/redox
REDOX_PKG=https://static.redox-os.org/pkg
REDOX_INSTALLER=${REDOX_INSTALLER:-$HOME/backotter-work/tools/redox/bin/redox_installer}
REDOXFS=${REDOXFS:-$(dirname "$REDOX_INSTALLER")/redoxfs}

here=$(dirname "$(realpath "$0")")
PATH=$PATH:/sbin:/usr/sbin
die() { echo "make-redox: $*" >&2; exit 1; }
# --retry-all-errors: a name that doesn't resolve, now and then, too.
fetch() { curl -fsSL --retry 4 --retry-all-errors --retry-delay 5 "$@"; }

[[ $# -eq 2 ]] || die "usage: $0 RELEASE WORK_DIR"
release=$1
[[ $release =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "RELEASE looks like 0.9.0, not '$release'"
guest_name=redox
DISK=$guest_name.qcow2
tools=()
# cargo install's record of each tool, where there is one (neither has --version).
check_tool() {  # BINARY CRATE VERSION
    [[ -x $1 ]] ||
        die "no $1; install it with: cargo +1.94.1 install --locked --version $3 --root ~/backotter-work/tools/redox $2"
    local crates
    crates=$(dirname "$(dirname "$(realpath "$1")")")/.crates.toml
    if [[ -f $crates ]]; then
        grep -q "^\"$2 $3 " "$crates" || die "$1 isn't version $3 of $2 (see $crates)"
        tools+=("$2 $3 $(realpath "$1")")
    else
        tools+=("$2 $3? $(realpath "$1") (no .crates.toml to check the version by)")
    fi
}
check_tool "$REDOX_INSTALLER" redox_installer "$INSTALLER_VERSION"
check_tool "$REDOXFS" redoxfs "$REDOXFS_VERSION"
out=$2
mkdir -p "$out"
cd "$out"
out=$PWD
[[ -e $DISK ]] && die "$out/$DISK already exists; move it away to rebuild"
# QEMU's -chardev options are split at commas.
[[ $out != *,* ]] || die "WORK_DIR's path can't hold a comma (QEMU's -chardev): $out"

# The installer's fixed /tmp paths: one build at a time on this host.
exec 9>/tmp/bko-make-redox.lock
flock -n 9 || die "another make-redox.sh runs on this host (/tmp/bko-make-redox.lock)"

work=$(mktemp -d)
raw=$(mktemp -p . --suffix=.raw disk.XXXXXX)
qemu_pid="" fs_pid=""
cleanup() {
    # || true: under set -e, a kill of a process that's already gone would
    # end the trap before the rest.
    [[ -z $qemu_pid ]] || kill "$qemu_pid" 2>/dev/null || true
    if [[ -n $fs_pid ]]; then
        fusermount3 -u "$work/mnt" 2>/dev/null || true
        wait "$fs_pid" 2>/dev/null || true
    fi
    rm -rf "$work" "$raw" /tmp/pkg_download
}
trap cleanup EXIT
printf '%s\n' "${tools[@]}" >redox-tools

# --- Fetch and check the configs and the package key -------------------------------
echo "Fetching upstream's configs and package index"
commit=$(fetch "https://gitlab.redox-os.org/api/v4/projects/redox-os%2Fredox/repository/commits/master" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])') || die "can't get upstream's newest commit"
[[ $commit =~ ^[0-9a-f]{40}$ ]] || die "upstream's newest commit looks wrong: '$commit'"
mkdir "$work/config"
for f in base minimal server; do
    fetch -o "$work/config/$f.toml" "$REDOX_GIT/-/raw/$commit/config/$f.toml" ||
        die "could not download config/$f.toml at $commit"
done
grep -qx "VERSION_ID=\"$release\"" "$work/config/base.toml" ||
    die "upstream's base.toml (at $commit) is no longer $release: $(grep '^VERSION_ID=' "$work/config/base.toml" || echo "no VERSION_ID")"
# The include chain (server, minimal, base) is what bko.toml builds on;
# fail here, not in the installer, if upstream renames it.
grep -qx 'include = \["minimal.toml"\]' "$work/config/server.toml" &&
    grep -qx 'include = \["base.toml"\]' "$work/config/minimal.toml" ||
    die "upstream's server.toml no longer includes minimal.toml, base.toml"
fetch -o "$work/key.toml" "$REDOX_PKG/id_ed25519.pub.toml" || die "could not download Redox's package key"
grep -qx "pkey = \"$REPO_KEY\"" "$work/key.toml" || die "Redox's package key changed: $(cat "$work/key.toml")"
fetch -o "$work/repo.toml" "$REDOX_PKG/x86_64-unknown-redox/repo.toml" || die "could not download the package index"
for p in openssh bash kernel bootloader; do
    grep -q "^$p = \"[0-9a-f]\{64\}\"" "$work/repo.toml" || die "the package index has no $p today"
done
rm -rf redox-config
cp -r "$work/config" redox-config
echo "$commit" >redox-config.commit
cp "$work/repo.toml" redox-repo.toml
(cd "$work" && sha256sum config/*.toml key.toml repo.toml) >redox-fetched.sha256
# The password prompts on (see redox/bko.toml): base.toml's prompt = false
# is the one the installer keeps, whatever bko.toml says.
grep -qx 'prompt = false' "$work/config/base.toml" || die "upstream's base.toml no longer says prompt = false"
sed -i 's/^prompt = false$/prompt = true/' "$work/config/base.toml"

# --- Credentials and the installer's config ----------------------------------------
(
    umask 077
    [[ -e id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name test guest" -f id_ed25519
    openssl rand -base64 18 | tr -dc A-Za-z0-9 >root-password
    ssh-keygen -q -t ed25519 -N '' -C "root@$guest_name" -f "$work/hostkey"
)
[[ $(wc -c <root-password) -ge 16 ]] || die "no password made"
# No secret goes in: the password is typed at the installer's prompts, and
# the host key's private half written in after the install.
CONFIG_DIR=$work/config HOSTNAME=$guest_name \
    AUTHORIZED_KEY=$(<id_ed25519.pub) HOST_KEY_PUB=$(<"$work/hostkey.pub") \
    python3 -c '
import os, re, sys
s = open(sys.argv[1]).read()
s = re.sub(r"@([A-Z_]+)@", lambda m: os.environ.get(m.group(1), m.group(0)), s)
open(sys.argv[2], "w").write(s)' "$here/redox/bko.toml" "$work/bko.toml"
if grep -n '@[A-Z_]\+@' "$work/bko.toml"; then die "bko.toml placeholders left unfilled"; fi
"$REDOX_INSTALLER" --config="$work/bko.toml" --list-packages >"$work/packages" 2>&1 ||
    die "redox_installer can't read bko.toml: $(tail -n 3 "$work/packages")"
grep -qx openssh "$work/packages" || die "openssh isn't in the package list: see $work/packages"

# --- Install ------------------------------------------------------------------------
# A disk file that exists keeps its size and is partitioned whole.  A try
# that failed in a download starts again on a new file; what it downloaded
# stays in the installer's cache, which starts empty.
echo "Installing; the installer's log goes to $out/install.log"
rm -rf /tmp/pkg_download
: >install.log
start_time=$SECONDS
for try in $(seq "$INSTALL_TRIES"); do
    echo "=== make-redox: redox_installer, try $try" >>install.log
    rm -f "$raw"
    truncate -s "$INSTALL_SIZE" "$raw"
    python3 "$here/redox/answer-passwords.py" root-password "$INSTALL_TIMEOUT" \
        "$REDOX_INSTALLER" --config="$work/bko.toml" "$raw" </dev/null >>install.log 2>&1 &&
        break
    tail -n 8 install.log | grep -q 'Download error' && (( try < INSTALL_TRIES )) ||
        die "redox_installer failed or timed out; see $out/install.log"
    echo "A download failed; trying again"
done
echo "Installed in $((SECONDS - start_time)) s"
# The last try's log: both passwords set, by four answers.
last_try=$(awk '/^=== make-redox: redox_installer, try/ { s = "" } { s = s $0 "\n" } END { printf "%s", s }' install.log | tr -d '\r')
for u in root user; do
    grep -A1 "Adding user $u:\$" <<<"$last_try" | grep -q 'Password: set' ||
        die "the installer didn't set $u's password; see $out/install.log"
done
grep -qx 'answer-passwords.py: 4 answers typed' <<<"$last_try" ||
    die "not 4 passwords typed at the installer's prompts; see $out/install.log"

# The layout the boot loader needs: RedoxFS at 2 MiB, three partitions.
layout=$(sfdisk -J "$raw" | python3 -c '
import json, sys
t = json.load(sys.stdin)["partitiontable"]
print(t["label"], " ".join("%s:%s" % (p["start"], p.get("name", "")) for p in t["partitions"]))') ||
    die "can't read the installer's partition table"
[[ $layout == "gpt 34:BIOS 2048:EFI $REDOXFS_START:REDOX" ]] ||
    die "the installer wrote an unexpected layout: $layout"

# --- What was installed --------------------------------------------------------------
# The installer fetched its own package index, maybe a newer daily build
# than redox-repo.toml: what it installed is in the image's pkg state.
# Also sshd's private host key goes in here, and /filesystem.toml is
# checked.
mkdir "$work/mnt"
"$REDOXFS" -d "$raw" "$work/mnt" >"$work/redoxfs.log" 2>&1 &
fs_pid=$!
for _ in $(seq 30); do
    mountpoint -q "$work/mnt" && break
    kill -0 "$fs_pid" 2>/dev/null || break
    sleep 1
done
mountpoint -q "$work/mnt" || die "redoxfs can't mount what the installer made: $(tail -n 3 "$work/redoxfs.log")"
cp "$work/mnt/etc/pkg/packages.toml" redox-packages.toml || die "the image has no /etc/pkg/packages.toml"
# sshd's host key, root's (redoxfs's FUSE side writes uid 0), mode 0600.
key=$work/mnt/etc/ssh/ssh_host_ed25519_key
[[ -f $key.pub && ! -e $key ]] || die "the image's /etc/ssh isn't as bko.toml makes it"
# Made 0600 by the umask: redoxfs's FUSE side lets no chmod through.
(umask 077 && cat "$work/hostkey" >"$key") || die "can't write sshd's host key into the image"
[[ $(stat -c '%a %u' "$key") == "600 0" ]] || die "sshd's host key is $(stat -c '%a, uid %u' "$key") in the image, not 600, uid 0"
cmp -s "$work/hostkey" "$key" || die "sshd's host key reads back wrong from the image"
# /filesystem.toml as bko.toml overwrote it.
fs_mode=$(stat -c %a "$work/mnt/filesystem.toml") || die "no /filesystem.toml in the image"
[[ $fs_mode == 600 ]] || die "/filesystem.toml is mode $fs_mode, not 600: did bko.toml's overwrite fail?"
grep -q 'make-redox.sh replaced it' "$work/mnt/filesystem.toml" &&
    ! grep -qE 'PRIVATE KEY|^\[users' "$work/mnt/filesystem.toml" ||
    die "/filesystem.toml isn't bko.toml's note: did its overwrite fail?"
fusermount3 -u "$work/mnt" || die "can't unmount redoxfs from $work/mnt"
wait "$fs_pid" || die "redoxfs failed: $(tail -n 3 "$work/redoxfs.log")"
fs_pid=""
# installed PACKAGES_TOML INDEX: the installed packages INDEX doesn't have
# at the installed blake3 (none: they came from it).
installed() {
    python3 -c '
import sys, tomllib
inst = tomllib.load(open(sys.argv[1], "rb"))["installed"]
index = tomllib.load(open(sys.argv[2], "rb"))["packages"]
print(" ".join(sorted(n for n, p in inst.items() if index.get(n) != p.get("blake3"))))' "$@"
}
others=$(installed redox-packages.toml redox-repo.toml) || die "can't read redox-packages.toml or redox-repo.toml"
if [[ -n $others ]]; then
    fetch -o "$work/repo.toml" "$REDOX_PKG/x86_64-unknown-redox/repo.toml" || die "could not download the package index again"
    still=$(installed redox-packages.toml "$work/repo.toml") || die "can't read the package index"
    [[ -z $still ]] ||
        die "the installed packages $still are in neither the package index fetched before the install nor the one after it"
    echo "The package index moved on during the install, from build $(sed -n 's/^build_id = "\(.*\)"/\1/p' redox-repo.toml)" \
        "to $(sed -n 's/^build_id = "\(.*\)"/\1/p' "$work/repo.toml"), which was installed; redox-repo.toml is that one"
    cp "$work/repo.toml" redox-repo.toml
    sed -i '/ repo\.toml$/d' redox-fetched.sha256
    (cd "$work" && sha256sum repo.toml) >>redox-fetched.sha256
fi
build_id=$(sed -n 's/^build_id = "\([0-9a-f]*\)"$/\1/p' redox-repo.toml)
[[ -n $build_id ]] || die "redox-repo.toml has no build_id"
echo "Installed: $(python3 -c 'import sys, tomllib; print(len(tomllib.load(open(sys.argv[1], "rb"))["installed"]))' \
    redox-packages.toml) packages of build $build_id"

# --- The swap partition, after RedoxFS ---------------------------------------------
# The BIOS boot loader's first stage is LBA 0's first 440 bytes: they must
# survive sfdisk's rewrites of the GPT and its protective MBR.
head -c 440 "$raw" >"$work/mbr-code"
truncate -s "$DISK_SIZE" "$raw"
# (Their output only on failure: on the grown file, sfdisk warns of the
# protective MBR's size and the backup GPT's place, which this fixes.)
msg=$(sfdisk -q --relocate gpt-bak-std "$raw" 2>&1) || die "sfdisk couldn't move the backup GPT to the disk's new end: $msg"
msg=$(echo "size=$SWAP_SIZE, type=$SWAP_TYPE, name=swap" | sfdisk -q -a "$raw" 2>&1) ||
    die "sfdisk couldn't add the swap partition: $msg"
cmp -s -n 440 "$work/mbr-code" "$raw" || dd if="$work/mbr-code" of="$raw" conv=notrunc status=none
cmp -s -n 440 "$work/mbr-code" "$raw" || die "the boot code in LBA 0 didn't survive"
swap=$(sfdisk -J "$raw" | python3 -c '
import json, sys
p = json.load(sys.stdin)["partitiontable"]["partitions"][int(sys.argv[1]) - 1]
print(p["start"], p["size"])' "$SWAP_PART") || die "can't read partition $SWAP_PART's start and size back from sfdisk"
read -r swap_start swap_sectors <<<"$swap"
[[ $swap_start =~ ^[0-9]+$ && $swap_sectors =~ ^[0-9]+$ ]] ||
    die "partition $SWAP_PART's start and size read as '$swap'"
(( swap_sectors * 512 == $(numfmt --from=iec "$SWAP_SIZE") && swap_start % 8 == 0 )) ||
    die "partition $SWAP_PART is $swap_sectors sectors at $swap_start, not $SWAP_SIZE"
truncate -s $((swap_sectors * 512)) "$work/swap"
mkswap -q -L swap "$work/swap" || die "mkswap failed"
# Only its first page holds anything (the signature); the rest stays sparse.
dd if="$work/swap" of="$raw" bs=4096 count=1 seek=$((swap_start / 8)) conv=notrunc status=none
rm -f "$work/swap"
sfdisk -V "$raw" >/dev/null || die "sfdisk finds the partition table broken"

# --- No secrets in the open ------------------------------------------------------------
# Every block of the disk that holds data, used or free (a backup of a
# partition it can't read takes it whole): the password nowhere (/etc/shadow
# has its hash), the host key's lines once (/etc/ssh/ssh_host_ed25519_key).
# (The host key's first line is every OpenSSH ed25519 key's header, and
# its short last line holds the comment: the full lines in between.)
counts=$({ cat root-password; echo; sed '1,2d;$d' "$work/hostkey" | awk 'length($0) >= 64'; } | python3 -c '
import os, sys
needles = [l.encode() for l in sys.stdin.read().split("\n") if l]
fd = os.open(sys.argv[1], os.O_RDONLY)
size, counts, off = os.fstat(fd).st_size, [0] * len(needles), 0
while off < size:
    try:
        start = os.lseek(fd, off, os.SEEK_DATA)
    except OSError:             # ENXIO: only a hole after off
        break
    end = os.lseek(fd, start, os.SEEK_HOLE)
    pos, tail = start, b""
    while pos < end:
        chunk = os.pread(fd, min(16 << 20, end - pos), pos)
        if not chunk:
            break
        pos += len(chunk)
        buf = tail + chunk
        for i, n in enumerate(needles):   # what lies wholly in tail was counted
            counts[i] += buf.count(n) - tail.count(n)
        tail = buf[-256:]
    off = end
print(*counts)' "$raw") || die "can't read $raw for the secrets check"
read -r pw_count key_counts <<<"$counts"
[[ -n $key_counts ]] || die "no lines of the host key to look for"
[[ $pw_count == 0 ]] || die "root's password is in the image $pw_count time(s), in the clear"
for c in $key_counts; do
    [[ $c == 1 ]] || die "a line of sshd's host key is in the image $c time(s), not once (counts: $key_counts)"
done
echo "No password and one copy of the host key in the image's $(du -h "$raw" | cut -f1) of data"

qemu-img convert -f raw -O qcow2 "$raw" "$DISK"
rm -f "$raw"
echo "--q35 --no-vga --disk-bus sata --nic e1000" >vm-run-options

# --- First boot ----------------------------------------------------------------------
# On the serial console, a socket for redox/console.py, its output also in
# first-boot-serial.log.  Power off from there, never by killing QEMU:
# RedoxFS is copy-on-write, but a builder shouldn't count on that.
port=${SSH_PORT:-$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}
sock=$work/console.sock
console() { python3 "$here/redox/console.py" "$sock" root-password "$@"; }
rm -f first-boot-serial.log first-boot.log no-ssh
echo "First boot; its serial console goes to $out/first-boot-serial.log"
start_time=$SECONDS
# -no-reboot: a triple fault ends QEMU instead of looping.
# shellcheck disable=SC2046
SSH_PORT=$port timeout "$FIRST_BOOT_TIMEOUT" "$here/vm-run" $(<vm-run-options) "$DISK" -- \
    -chardev "socket,id=console,path=$sock,server=on,wait=off,logfile=$out/first-boot-serial.log" \
    -serial chardev:console -monitor none -no-reboot </dev/null >"$work/vm-run.log" 2>&1 &
qemu_pid=$!
if ! console login "$LOGIN_TIMEOUT" >"$work/login.log" 2>&1; then
    kill -0 "$qemu_pid" 2>/dev/null || die "QEMU ended in the first boot: $(tail -n 3 "$work/vm-run.log")"
    grep -aqs 'Arrow keys and enter select mode' first-boot-serial.log &&
        die "the boot loader waits at its video-mode menu: vm-run without --no-vga?"
    die "no root login on the serial console ($(tail -n 1 "$work/login.log")); see $out/first-boot-serial.log"
fi
echo "Root's shell on the console after $((SECONDS - start_time)) s"

# SSH: started here (not at boot: see the header), sshd must listen; a
# login says whether it serves one, and what sshd logs, why not.
# In the background of root's shell, logging to the console as init's did.
console send '/usr/bin/sshd -D -e &' >/dev/null 2>&1 || die "can't start sshd on the console"
for _ in $(seq 60); do
    grep -aqs 'Server listening on 0\.0\.0\.0 port 22' first-boot-serial.log && break
    sleep 1
done
grep -aqs 'Server listening on 0\.0\.0\.0 port 22' first-boot-serial.log ||
    die "sshd doesn't listen on port 22; see $out/first-boot-serial.log"
# timeout: QEMU's user network accepts the connection before the guest
# is up, and Redox's sshd stalls the sessions it lets in.
ssh_ok=no
[[ $(timeout 30 ssh -i id_ed25519 -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -o BatchMode=yes root@127.0.0.1 uname -s </dev/null 2>/dev/null) == Redox ]] && ssh_ok=yes
if [[ $ssh_ok == no ]]; then
    sleep 2
    reason=$(grep -aoE "$SSHD_FATAL" first-boot-serial.log | head -n 1) || reason=""
    # (No ssh-keyscan check: in tests it got the host key once, and nothing
    # other times, as sshd-auth died at its chroot first, 2026-11.)
    echo "sshd listens on port 22, but serves no logins: root's key login got no session; sshd logged: ${reason:-nothing that says why}" >no-ssh
fi

# What vm-update and compare need, once SSH works: sh -s, poweroff, reboot,
# pkg.  Each line of a command's output is labelled, in first-boot.log:
# what the guest logs to the console meanwhile can land in it.
check() {  # LABEL COMMAND
    local o rc=0
    o=$(console run "$2") || rc=$?
    printf '%s\n' "$o" | sed "s|^|$1: |"
    [[ $rc == 0 ]] || { echo "$1: failed ($rc)"; return 1; }
}
(
    check uname-s 'uname -s' && check uname-a 'uname -a' && check hostname 'cat /etc/hostname; echo' &&
        check sh-s "printf 'echo ok\\n' | sh -s" && check command-v 'command -v sh pkg shutdown poweroff reboot' &&
        check grep 'echo bko-grep-works | grep -c bko-grep' &&
        check df 'df -h /' && check os-release 'cat /etc/os-release' || exit 1
    echo "package index build: $build_id"
) >first-boot.log 2>&1 || die "the first boot's checks failed on the console; see $out/first-boot.log"
grep -qx 'uname-s: Redox' first-boot.log || die "uname -s doesn't say Redox; see $out/first-boot.log"
grep -qx "hostname: $guest_name" first-boot.log || die "the host name isn't $guest_name; see $out/first-boot.log"
grep -qx 'sh-s: ok' first-boot.log || die "sh -s doesn't work; see $out/first-boot.log"
grep -qx 'grep: 1' first-boot.log || die "grep doesn't run in the guest; see $out/first-boot.log"
for c in sh pkg shutdown poweroff reboot; do
    grep -qx "command-v: /usr/bin/$c" first-boot.log || die "the guest has no $c; see $out/first-boot.log"
done
grep -qE '^df: / +[0-9.]+G ' first-boot.log || die "df doesn't show /; see $out/first-boot.log"

console send poweroff >/dev/null 2>&1 || die "can't type poweroff on the console"
for _ in $(seq "$POWEROFF_TIMEOUT"); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
kill -0 "$qemu_pid" 2>/dev/null && die "the first boot didn't power off within $POWEROFF_TIMEOUT s"
wait "$qemu_pid" || die "QEMU failed in the first boot: $(tail -n 3 "$work/vm-run.log")"
qemu_pid=""
echo "First boot done in $((SECONDS - start_time)) s"

qemu-img snapshot -c fresh-install "$DISK"
echo "Done: $out/$DISK ($(du -h "$DISK" | cut -f1) on disk), snapshot \"fresh-install\", build $build_id"
if [[ $ssh_ok == yes ]]; then
    echo "SSH serves logins now: ssh -i $out/id_ed25519 -p PORT root@127.0.0.1 (update the header, and the guest's tools)"
else
    echo "SSH DOESN'T SERVE LOGINS: $(<no-ssh)"
    echo "The way in is the serial console (root, $out/root-password): see the header"
fi
