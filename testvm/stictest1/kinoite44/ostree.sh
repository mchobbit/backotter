#!/bin/bash
# ostree's layout for kinoite44's btrfs fixture (docs/design/bazzite.md,
# "SticTest1's kinoite44 check"), under $1 (fixture.sh's TREE/ostree):
#
#   made/ostree: a bare repository made by the guest's own ostree from real
#     files of the booted deployment (labels by the policy, setuid binaries,
#     a capability, a mode-0 shadow, the bin/lib/usr/sbin symlinks), its
#     commit checked out twice as `ostree admin deploy` leaves it
#     (deploy/default/deploy/C.0 and C.1: every regular file and symlink
#     object hardlinked into both, nlink 3; each its own etc; the roots +i),
#     and the boot.1 -> boot.1.1 chain.  `ostree fsck` proves it whole
#     before the backup and after each restore (inner.sh).
#   real/ostree: real objects of /sysroot/ostree/repo and both deployments'
#     paths to them, copied in one cp -a (so linked as on the guest, nlink 3
#     within the copy, labels kept), the deployment roots +i.
#
# Prints what it made on stdout; what it skipped on stderr.
set -u
T=$1
skip() { echo "ostree: skipped: $*" >&2; }
REPO=/sysroot/ostree/repo
# The booted deployment and its commit: /run/ostree-booted's system, from
# `ostree admin status` (its "* STATEROOT CSUM.SERIAL" line).
read -r ST DEP < <(ostree admin status 2>/dev/null | awk '$1 == "*" { print $2, $3; exit }')
[[ -n ${DEP:-} && -d /sysroot/ostree/deploy/$ST/deploy/$DEP ]] || { skip "no booted deployment"; exit 1; }
C=${DEP%.*}
mapfile -t DEPS < <(cd /sysroot && find ostree/deploy/$ST/deploy -mindepth 1 -maxdepth 1 -type d -regex ".*/$C\.[0-9]+" | sort)
# Regular files (setuid 4755 and 4111, a capability, mode 0, a shared
# license file with 191 links on the guest) and symlinks of the commit.
# The largest file system module, xz-compressed already: data that btrfs's
# heuristic takes for compressible and that doesn't compress, so Linux
# 6.12's btrfs marks such a file FS_NOCOMP_FL itself when it is written
# under compress= (B4 saw 63,720 files flagged by the host's restore; the
# restores onto fstab's compress=zstd:1 in inner.sh run the guest's kernel).
FILES="usr/bin/bash usr/bin/passwd usr/bin/su usr/bin/sudo usr/bin/newuidmap usr/lib/os-release
       usr/lib64/libc.so.6 usr/libexec/sudo/sudoers.so usr/share/licenses/bash/COPYING usr/etc/shadow"
ko=$(cd "/sysroot/ostree/deploy/$ST/deploy/$DEP" && ls -S usr/lib/modules/*/kernel/fs/*/*.ko.xz 2>/dev/null | head -1)
[[ -n $ko ]] && FILES="$FILES $ko" || skip "a compressed kernel module"
LINKS="bin lib usr/sbin"

# real/: one cp -a of every object and both deployments' paths to it.
srcs=()
for p in $FILES $LINKS; do
    cs=$(ostree --repo=$REPO ls -C "$C" "/$p" 2>/dev/null | awk '{ print $5; exit }')
    [[ ${#cs} == 64 && -e $REPO/objects/${cs:0:2}/${cs:2}.file || -L $REPO/objects/${cs:0:2}/${cs:2}.file ]] ||
        { skip "object of /$p"; continue; }
    srcs+=("ostree/repo/objects/${cs:0:2}/${cs:2}.file")
    for d in "${DEPS[@]}"; do srcs+=("$d/$p"); done
done
mkdir -p "$T/real"
(cd /sysroot && cp -a --parents "${srcs[@]}" "$T/real/") || skip "cp -a of the real objects"
for d in "${DEPS[@]}"; do chattr +i "$T/real/$d" 2>/dev/null || skip "chattr +i real/$d"; done
echo "real: ${#DEPS[@]} deployments of $ST/$C, ${#srcs[@]} paths"

# made/: the guest's ostree makes a repository of those files and checks
# its commit out twice.
stage=$(mktemp -d /tmp/bko-ostree-stage.XXXXXX)
chmod 755 "$stage"   # the commit's root, as a deployment's
(cd "/sysroot/ostree/deploy/$ST/deploy/$DEP" && cp -a --parents $FILES $LINKS "$stage/") || skip "staging the commit's files"
M=$T/made/ostree R=$T/made/ostree/repo
mkdir -p "$M/deploy/default/deploy"
if ostree --repo="$R" init --mode=bare &&
    ostree --repo="$R" commit --selinux-policy=/ -b bko --tree=dir="$stage" >/dev/null; then
    MC=$(ostree --repo="$R" rev-parse bko)
    for n in 0 1; do
        D=$M/deploy/default/deploy/$MC.$n
        ostree --repo="$R" checkout -H bko "$D" || skip "checkout $n"
        # Each deployment's own etc, from usr/etc, as ostree admin makes it.
        cp -a "$D/usr/etc" "$D/etc" && echo "$n" >"$D/etc/bko-deployment" && chcon --reference="$D/usr/etc" "$D/etc/bko-deployment"
    done
    B=$M/boot.1.1/default/$(printf '%s' "$MC" | sha256sum | cut -c1-64)
    mkdir -p "$B"
    ln -s "../../../deploy/default/deploy/$MC.1" "$B/0"; ln -s "../../../deploy/default/deploy/$MC.0" "$B/1"
    ln -s boot.1.1 "$M/boot.1"
    # ostree fsck before the backup, so whatever it writes is the source's.
    ostree --repo="$R" fsck >&2 || skip "ostree fsck of made/ (source)"
    for n in 0 1; do chattr +i "$M/deploy/default/deploy/$MC.$n" || skip "chattr +i made/$n"; done
    echo "made: commit $MC, $(find "$R/objects" -name '*.file' | wc -l) file objects, checkouts .0 .1"
else
    skip "ostree init/commit of made/"
fi
rm -rf "$stage"
exit 0
