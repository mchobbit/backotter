# GuestTest2's boot checks (contract 2.1 step 3), run in the pre-update boot (in the before
# hook) and again after the reboot, as root over ssh sh -s.  guest/job.sh puts RULES="B-lin
# M-lin K-deb ..." in front and, on Linux, compare/boot-state.sh as a function boot_state.
# It prints sections, and guest_judge.judge_after compares the two readings on the host.
# Direct reads only: no dump, and nothing here changes the guest.  POSIX sh.
PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin:/bin:/boot/system/bin:$PATH
export PATH
has_rule() { case " $RULES " in *" $1 "*) return 0 ;; esac; return 1; }
qt() { sed 's/^===/= ==/'; }
cmd() { echo "=== cmd $1"; }

cmd after-rules; echo "$RULES"
# --- service state (ht1-bsd.sh's SERVICE_STATE and its OpenBSD and illumos forms)
if has_rule B-lin; then
    cmd boot-state; boot_state 2>&1 | qt
elif has_rule B-bsd; then
    cmd boot-state
    svc=$(service -e 2>/dev/null)
    for s in $svc; do
        o=$($s status 2>&1)
        case $o in *"is not running"*) echo "down ${s##*/}" ;; esac
    done | sort
    n=$(($(echo "$svc" | wc -w)))
    if [ "$n" -gt 0 ]; then echo "ok $n services checked"; else echo "error service -e listed nothing"; fi
elif has_rule B-obsd; then
    cmd boot-state
    rcctl ls failed 2>/dev/null | sed 's/^/down /' | sort
    n=$(($(rcctl ls on 2>/dev/null | wc -l)))
    if [ "$n" -gt 0 ]; then echo "ok $n daemons checked"; else echo "error rcctl lists no daemon"; fi
elif has_rule B-ill; then
    cmd boot-state
    i=0
    while svcs -H -o state | grep -q '\*' && [ $i -lt 60 ]; do sleep 2; i=$((i + 1)); done
    svcs -H -o state,fmri | awk '$1 != "online" && $1 != "disabled" { print "down " $2 }' | sort
    n=$(($(svcs -H -o state 2>/dev/null | wc -l)))
    if [ "$n" -gt 0 ]; then echo "ok $n services checked"; else echo "error svcs lists nothing"; fi
elif has_rule B-alpbsd; then
    # AlpineBSD has s6, not rc.d's service(8): what the default bundle wants and isn't up.
    cmd boot-state
    if command -v s6-rc >/dev/null 2>&1; then
        want=$(s6-rc-db all-dependencies default 2>/dev/null)
        s6-rc -da list 2>/dev/null | while read -r n; do
            case "
$want
" in *"
$n
"*) echo "down $n" ;; esac
        done | sort
        n=$(($(printf '%s\n' "$want" | grep -c .)))
        if [ "$n" -gt 0 ]; then echo "ok $n services checked"; else echo "error s6-rc-db lists nothing in default"; fi
    else
        echo "error no s6-rc"
    fi
fi
# --- fstab: verified, every mount mounted
cmd fstab-check
if has_rule M-lin || has_rule M-zfs || has_rule M-alp; then
    if ! has_rule M-alp; then
        o=$(findmnt --verify 2>&1); rc=$?
        if [ $rc -ne 0 ]; then
            echo "findmnt --verify failed"
            echo "$o" | grep -E '\[E\]' | sed -E 's/^[[:space:]]*/findmnt: /; s/[[:space:]]+$//' | sort -u
        fi
    fi
    m=0
    [ -r /etc/fstab ] || echo "no readable /etc/fstab"
    for t in $(awk '$1 !~ /^#/ && $2 ~ /^\// && $3 != "swap" && $4 !~ /noauto/ { print $2 }' /etc/fstab); do
        if mountpoint -q "$t"; then m=$((m + 1)); else echo "fstab mount $t not mounted"; fi
    done
    echo "ok $m fstab mounts mounted"
elif has_rule M-bsd; then
    m=0
    [ -r /etc/fstab ] || echo "no readable /etc/fstab"
    for t in $(awk '$1 !~ /^#/ && $2 ~ /^\// && $3 != "swap" && $4 !~ /noauto/ { print $2 }' /etc/fstab); do
        if mount | sed -e 's/ type [^ ]* (.*//' -e 's/ (.*//' -e 's/^.* on //' | grep -qx "$t"; then m=$((m + 1))
        else echo "fstab mount $t not mounted"; fi
    done
    echo "ok $m fstab mounts mounted"
elif has_rule M-ill; then
    m=0
    [ -r /etc/vfstab ] || echo "no readable /etc/vfstab"
    for t in $(awk '$1 !~ /^#/ && $3 ~ /^\// && $4 != "swap" && $6 == "yes" { print $3 }' /etc/vfstab); do
        if mount -p | awk '{ print $3 }' | grep -qx "$t"; then m=$((m + 1))
        else echo "vfstab mount $t not mounted"; fi
    done
    echo "ok $m vfstab mounts mounted"
elif has_rule M-haiku; then
    if df | grep -q "/dev/disk/"; then echo "ok boot volume mounted"; else echo "no boot volume"; fi
fi
# --- the mount list, in mount order
cmd mounts
if [ -r /proc/self/mountinfo ]; then
    echo "format mountinfo"; qt </proc/self/mountinfo
else
    case $(uname) in
    FreeBSD|DragonFly) echo "format mount-p"; mount -p | qt ;;
    SunOS) echo "format vfstab-p"; mount -p | qt ;;
    Haiku) echo "format none" ;;
    *) echo "format mount"; mount | qt ;;
    esac
fi
# --- the running kernel against the installed ones
same() {  # FILE: does the running kernel's version string appear in FILE?
    # uname(3) turns the version's newline and tab into spaces (NetBSD: `... UTC 2026  mkrepro@...`),
    # so cutting the builder leaves trailing blanks that the kernel file's string doesn't have
    v=$(uname -v | sed 's/[[:space:]][^[:space:]]*@.*//; s/[[:space:]]*$//')
    if [ ! -r "$1" ] || [ -z "$v" ]; then echo "unverified can't read $1"
    elif grep -aqF "$v" "$1"; then echo "match yes"
    else echo "match no"; fi
}
if ! has_rule K-none && ! has_rule K-haiku; then
    cmd kernel
    r=$(uname -r)
    if has_rule K-deb || has_rule K-fed || has_rule K-void || has_rule K-moss; then
        echo "running $r"
        for d in /lib/modules/*/ /usr/lib/modules/*/; do
            [ -e "${d}kernel" ] || [ -e "${d}vmlinuz" ] || [ -e "${d}modules.dep" ] || continue
            d=${d%/}
            echo "installed ${d##*/}"
        done | sort -u
    elif has_rule K-ostree; then
        # ostree: /usr/lib/modules is the booted deployment's alone, so what tells is which
        # deployment booted.  booted and default as CHECKSUM.SERIAL (the default is the first
        # deployment that isn't staged: what GRUB boots), and grubenv's keys that would make
        # bootupd's static grub.cfg boot something else (B6: it takes `default`).
        echo "running $r"
        for d in /usr/lib/modules/*/; do
            [ -e "${d}vmlinuz" ] || [ -e "${d}modules.dep" ] || continue
            d=${d%/}
            echo "installed ${d##*/}"
        done | sort -u
        rpm-ostree status --json 2>/dev/null | python3 -c 'import json, sys
try:
    d = [x for x in json.load(sys.stdin)["deployments"] if not x.get("staged")]
except Exception:
    d = []
i = lambda x: "%s.%s" % (x.get("checksum"), x.get("serial"))
b = [x for x in d if x.get("booted")]
if b: print("booted", i(b[0]))
if d: print("default", i(d[0]))'
        for f in /boot/grub2/grubenv /boot/grub/grubenv; do
            [ -r "$f" ] || continue
            sed -n -e 's/^default=.*/grubenv-key default/p' -e 's/^next_entry=.*/grubenv-key next_entry/p' "$f"
            break
        done
    elif has_rule K-pac || has_rule K-apk; then
        echo "running $r"
        if [ -e "/usr/lib/modules/$r/vmlinuz" ] || [ -e "/usr/lib/modules/$r/modules.dep" ] ||
            [ -e "/lib/modules/$r/modules.dep" ]; then echo "match yes"; else echo "match no"; fi
    elif has_rule K-nix; then
        a=$(readlink /run/booted-system/kernel) b=$(readlink /run/current-system/kernel)
        if [ -z "$a" ] || [ -z "$b" ]; then echo "unverified no /run/booted-system/kernel"
        elif [ "$a" = "$b" ]; then echo "match yes"
        else echo "match no"; fi
    elif has_rule K-fbsd; then
        echo "k $(freebsd-version -k 2>/dev/null)"
        echo "r $(freebsd-version -r 2>/dev/null)"
    elif has_rule K-obsd; then
        v=$(sysctl -n kern.version | head -n 1)
        if [ -z "$v" ] || [ ! -r /bsd ]; then echo "unverified can't read /bsd"
        elif grep -aqF "$v" /bsd; then echo "match yes"
        else echo "match no"; fi
    elif has_rule K-nbsd || has_rule K-alpbsd; then
        same /netbsd
    elif has_rule K-dfly; then
        same /boot/kernel/kernel
    elif has_rule K-ill; then
        echo "see beadm-after"
        cmd beadm-after; beadm list -H 2>/dev/null | qt
    fi
fi
