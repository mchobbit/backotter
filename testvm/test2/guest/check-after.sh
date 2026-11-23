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
# oneshot SCRIPT: is this rc.d script a boot-time task, not a daemon?  Its stop is a no-op
# (stop_cmd=":"), so nothing stays running, yet with command= set rc.subr's status looks for
# that command's process and says "is not running": FreeBSD's newsyslog (rotates once at boot,
# cron runs it after) and NetBSD's virecover.  ht1-bsd.sh compares two readings, so it doesn't care.
oneshot() { grep -Eq "^[[:space:]]*stop_cmd=[\"']?(:|true)[\"']?[[:space:]]*(#.*)?\$" "$1" 2>/dev/null; }
# nodaemon SCRIPT: is this rc.d/sendmail with mailer.conf handing sendmail(8) to dma, which runs
# no daemon?  DragonFly's MTA is dma, yet its rc.d/sendmail and rc.conf defaults are sendmail's:
# service -e lists the script (its last top-level rcvar= is sendmail_msp_queue_enable, YES by
# default) and status finds no sendmail_submit or clientmqueue daemon, as there is none to find.
nodaemon() { [ "${1##*/}" = sendmail ] && grep -Eq '^[[:space:]]*sendmail[[:space:]]+[^[:space:]]*/dma([[:space:]]|$)' "${MAILER_CONF:-/etc/mail/mailer.conf}" 2>/dev/null; }
# --- service state (ht1-bsd.sh's SERVICE_STATE and its OpenBSD and illumos forms)
if has_rule B-lin; then
    cmd boot-state; boot_state 2>&1 | qt
elif has_rule B-bsd; then
    cmd boot-state
    svc=$(service -e 2>/dev/null)
    for s in $svc; do
        oneshot "$s" && continue
        nodaemon "$s" && continue
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
    # Settled: nothing in transition (a * after its state) and nothing offline that SMF may
    # yet start, or 120 s.  Offline with no * is a service waiting on its dependencies, and
    # sshd answers before NWAM is done: each of its location changes (NoNet, then Automatic
    # once DHCP has answered) restarts ipfilter, which stops milestone/network (it depends on
    # ipfilter optional_all/restart) until SMF starts it again, 10 s later on a quiet host
    # and more on a busy one, with nothing in transition meanwhile (oi2610's "down
    # svc:/milestone/network:default", t2v-20261123T1729-550e; ht1-bsd.sh's BOOTSTATE waits
    # for the same reason).  The listing is the reading that passed, not one taken after it.
    # ill_coming FMRI...: of these offline services, the ones SMF may yet start, one a line.
    # Not one held for good: it requires (require_all) a service that is disabled, in
    # maintenance or absent, or one so held, as omnios58's cloud-init:init, modules and final
    # do behind initlocal (limits.json), which would cost 120 s at every reading.  What
    # svcs -l doesn't say counts as coming.
    ill_coming() {
        for f in "$@"; do
            echo "F $f"
            svcs -l "$f" 2>/dev/null |
                awk '$1 == "dependency" && $2 ~ /^require_all\// { for (k = 3; k < NF; k += 2) print "D", $k, $(k + 1) }'
        done | awk '
            $1 == "F" { cur = $2; name[++nf] = cur; next }
            $1 == "D" { k = ++nd[cur]; dep[cur, k] = $2; st[cur, k] = $3 }
            END {
                more = 1
                while (more) {
                    more = 0
                    for (j = 1; j <= nf; j++) {
                        f = name[j]
                        if (f in held) continue
                        for (k = 1; k <= nd[f]; k++) {
                            s = st[f, k]
                            if (s == "(disabled)" || s == "(maintenance)" || s == "(absent)" ||
                                (s == "(offline)" && (dep[f, k] in held))) {
                                # a dependency may name the service alone: svc:/system/cloud-init
                                g = f; sub(/:[^:\/]*$/, "", g)
                                held[f] = 1; held[g] = 1; more = 1
                                break
                            }
                        }
                    }
                }
                for (j = 1; j <= nf; j++) if (!(name[j] in held)) print name[j]
            }'
    }
    i=0
    while :; do
        s=$(svcs -H -o state,fmri 2>/dev/null)
        case $s in
        *'*'*) w=transition ;;
        *) w=$(ill_coming $(printf '%s\n' "$s" | awk '$1 == "offline" { print $2 }')) ;;
        esac
        [ -n "$w" ] && [ $i -lt 60 ] || break
        sleep 2; i=$((i + 1))
    done
    # legacy_run is an /etc/rc?.d script SMF ran at boot (an lrc: FMRI): ran is all a legacy
    # script can be, as illumos' own S20sysetup, S81dodatadm_udaplt and S89PRESERVE are
    printf '%s\n' "$s" | awk 'NF && $1 != "online" && $1 != "disabled" && $1 != "legacy_run" { print "down " $2 }' | sort
    n=$(($(printf '%s\n' "$s" | grep -c .)))
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
    if has_rule K-deb || has_rule K-fed || has_rule K-void || has_rule K-moss || has_rule K-gentoo || has_rule K-anduinos ||
        has_rule K-pve || has_rule K-pika || has_rule K-slackware || has_rule K-sdbu; then
        # (Slackware: uname -r is the version alone, 5.15.19, for huge and generic alike, and
        # /lib/modules/VERSION is kernel-modules'.)
        echo "running $r"
        for d in /lib/modules/*/ /usr/lib/modules/*/; do
            [ -e "${d}kernel" ] || [ -e "${d}vmlinuz" ] || [ -e "${d}modules.dep" ] || continue
            d=${d%/}
            echo "installed ${d##*/}"
        done | sort -u
    elif has_rule K-cbm; then
        # Solus (solus.md): clr-boot-manager's kernels by their file names in /usr/lib64/kernel
        # (com.solus-project.TYPE.VER-REL, which uname -r gives as VER-REL.TYPE).
        echo "running $r"
        for k in /usr/lib64/kernel/com.solus-project.*; do [ -e "$k" ] && echo "installed ${k##*/}"; done
    elif has_rule K-snapcore; then
        # Ubuntu Core (ubuntu-core.md): modeenv's current_kernels (NAME_REV.snap), the kernel
        # snap's revision booted (/snap/NAME/current), as NAME_REV.snap too, what the run GRUB's
        # EFI/ubuntu/kernel.efi links to, and its grubenv's kernel_status ("-": none).
        echo "running $r"
        b=/run/mnt/ubuntu-boot/EFI/ubuntu
        ks=$(sed -n 's/^current_kernels=//p' /var/lib/snapd/modeenv 2>/dev/null | head -n 1 | tr ',' ' ')
        for k in $ks; do echo "installed $k"; done
        k=${ks%% *} n=${k%_*.snap}
        v=$([ -n "$k" ] && readlink "/snap/$n/current" 2>/dev/null)
        [ -z "$v" ] || echo "booted ${n}_$v.snap"
        l=$(readlink "$b/kernel.efi" 2>/dev/null) l=${l%/kernel.efi}
        [ -z "$l" ] || echo "link ${l##*/}"
        s=$(sed -n 's/^kernel_status=//p' "$b/grubenv" 2>/dev/null | head -n 1)
        echo "status ${s:--}"
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
    elif has_rule K-abroot; then
        # ABRoot (Vanilla OS): the present root's own modules, the root booted (its label), and
        # the root the master grub.cfg's default entry leads to (`configfile "/LABEL/abroot.cfg"`).
        echo "running $r"
        for d in /usr/lib/modules/*/; do
            [ -e "${d}modules.dep" ] || [ -e "${d}kernel" ] || continue
            d=${d%/}
            echo "installed ${d##*/}"
        done | sort -u
        p=$(blkid -s LABEL -o value "$(findmnt -n -o SOURCE /)" 2>/dev/null)
        [ -z "$p" ] || echo "present $p"
        awk '/^[[:space:]]*#/ { next }
             /^[[:space:]]*menuentry / { n++; inside = 1; next }
             /^[[:space:]]*}/ { inside = 0; next }
             !inside && !seen && /^[[:space:]]*set default=/ { seen = 1; v = $0; sub(/.*set default=/, "", v); gsub(/"/, "", v) }
             inside && /^[[:space:]]*configfile / { c = $2; gsub(/"/, "", c); sub(/^\([^)]*\)/, "", c); split(c, a, "/"); cfg[n - 1] = a[2] }
             END { if (v ~ /^[0-9]+$/ && (v in cfg)) print "default", cfg[v] }' /boot/grub/grub.cfg 2>/dev/null
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
