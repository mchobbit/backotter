# In a fresh Fedora atomic guest, as root, from make-fedora.sh --ostree (over
# SSH, copied to /run first): the two steps that leave two deployments of one
# commit, as the Thinkpad's Bazzite has, differing in a kernel argument and
# in /etc.
#   sh ostree-boots.sh switch IMAGE
#       (first boot) point the booted deployment's origin at IMAGE's tag, in
#       place, and stage a second deployment with bko.rollback=1 added to its
#       kernel arguments
#   sh ostree-boots.sh mark IMAGE
#       (second boot) check that the second one booted, then write
#       /etc/bko-deployment in it: its checksum.serial
set -eu
step=$1 image=$2
case $step in
switch)
    rpm-ostree status
    # bootc's own way for an installer to name the tag the system follows,
    # without a new deployment.  It writes the origin file in /sysroot, which
    # is mounted read-only: rw in a mount namespace of its own, as ostree
    # does.  rpm-ostreed then has to read the origin again.
    unshare -m sh -c 'mount -o remount,rw /sysroot && exec bootc switch --mutate-in-place --transport registry "$1"' sh "$image"
    rpm-ostree reload
    rpm-ostree kargs --append=bko.rollback=1
    rpm-ostree status
    ;;
mark)
    rpm-ostree status
    # Two deployments, the first (the default) booted with serial 1, one
    # commit, both following the tag.
    got=$(rpm-ostree status --json | python3 -c '
import json, sys
d = json.load(sys.stdin)["deployments"]
b = [x for x in d if x["booted"]]
print(len(d), d[0]["booted"], b[0]["serial"] if b else "-", len({x["checksum"] for x in d}),
      " ".join(sorted({x.get("container-image-reference", "-") for x in d})))')
    want="2 True 1 1 ostree-unverified-registry:$image"
    [ "$got" = "$want" ] || { echo "deployments: '$got', expected '$want'" >&2; exit 1; }
    grep -qw bko.rollback=1 /proc/cmdline || { echo "bko.rollback=1 isn't on the booted command line" >&2; exit 1; }
    booted=$(ostree admin status | sed -n 's/^\* [^ ]* \([0-9a-f]*\.[0-9]*\)$/\1/p')
    [ -n "$booted" ] || { echo "no booted deployment in ostree admin status" >&2; exit 1; }
    echo "deployment $booted" >/etc/bko-deployment
    cat /etc/bko-deployment
    ostree admin status
    ;;
*) echo "ostree-boots.sh: no step '$step'" >&2; exit 2 ;;
esac
