# GuestTest2's new-guest checks (contract 2.1 step 4), a before hook of a guest whose spec
# says established: false.  vm-update's --push puts restic-bko and rustic-bko in /tmp.  It
# prints sections for guest_judge.new_guest_needs: the firmware it booted with, both archivers'
# --version, and a smoke backup of /etc and a check, the repositories in /tmp.
PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/gnu/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export PATH
qt() { sed 's/^===/= ==/'; }
cmd() { echo "=== cmd $1"; }

cmd firmware
case $(uname) in
Linux)
    if [ -d /sys/firmware/efi ]; then
        sb=$(od -An -tu1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | awk '{ print $NF }')
        if [ "$sb" = 1 ]; then echo uefi+secure-boot; else echo uefi; fi
    else
        echo bios
    fi ;;
FreeBSD)
    case $(sysctl -n machdep.bootmethod 2>/dev/null) in
    UEFI) echo uefi ;;
    *) echo bios ;;
    esac ;;
SunOS)
    if prtconf -v 2>/dev/null | grep -q efi-systab; then echo uefi; else echo bios; fi ;;
*) echo bios ;;
esac

R=/tmp/restic-bko U=/tmp/rustic-bko
chmod +x $R $U 2>/dev/null
cmd restic-version
if o=$($R version 2>&1); then printf '%s\n' "$o" | head -n 3 | qt; else echo "failed $R"; fi
cmd rustic-version
if o=$($U --version 2>&1); then printf '%s\n' "$o" | head -n 3 | qt; else echo "failed $U"; fi

pw=smoke-$$-$(date +%s)
RESTIC_PASSWORD=$pw RUSTIC_PASSWORD=$pw
export RESTIC_PASSWORD RUSTIC_PASSWORD
cmd smoke
rr=/tmp/bko-smoke-restic ru=/tmp/bko-smoke-rustic
rm -rf $rr $ru
if $R -r $rr --no-cache init -q >/dev/null 2>&1 && $R -r $rr --no-cache backup -q /etc >/dev/null 2>&1 &&
    $R -r $rr --no-cache check -q >/dev/null 2>&1; then echo "ok restic"; else echo "failed restic"; fi
if $U --no-progress --no-cache -r $ru init >/dev/null 2>&1 && $U --no-progress --no-cache -r $ru backup /etc >/dev/null 2>&1 &&
    $U --no-progress --no-cache -r $ru check >/dev/null 2>&1; then echo "ok rustic"; else echo "failed rustic"; fi
rm -rf $rr $ru
