# The apk guests' test tools (alpine324, alpine324-arm64, chimera, chimera-uefi,
# chimera-zfs: apk-tools 3), for a link that crawls.  Sourced by their check.sh
# (bash) after vm_boot, with GUEST and WORK set:
#
#   stic_apk_add PKG...   installs PKG... in the guest; status 0 when apk did.
#                         Else it prints one line,
#                           FAIL harness-tools: apk add PKG... failed
#                         with " (waiting on the network: LINE)" at its end when
#                         apk's log says the network stalled (LINE: its line) or
#                         the step's time ran out, which rows_stic.net_stall
#                         makes SticTest2's own stop, never the run-wide one.
#
# SweepTest2 t2s-20261119T0726-e436 (2026-11-19, the farm's internet at 10 to
# 200 KB/s, 20-50 % loss): apk add refreshed the image's indexes (older than
# apk's 4 h --cache-max-age: 3 MB on Alpine) and fetched the packages (19 MB on
# Alpine), the step's 300 s ran out, and two HARNESS rows stopped the sweep.
# So, in the guest:
#   - the index is the image's own (copied from /var/cache/apk, read with a
#     --cache-max-age of ten years): what the image's last update saw, as the
#     apt checks' "install from the image's lists" (5ee69f0, 0670cb7);
#   - the packages come from a cache on this host first (STIC_APK_CACHE,
#     default WORK/stictest1/GUEST/apk-cache: the .apk files apk kept, those
#     installed, taken back after each install that fetched any), so only a
#     package the image's index names anew is downloaded;
#   - 4 tries, 20 s apart, the last two with the mirror's index (the image's
#     may name packages gone from it), each apk network connection given up
#     after 120 s without progress; the step has STIC_APK_T (1800) s in all.
# The cache is no input of the cell's key: apk installs what the image's
# index names, and checks each package against it.

STIC_APK_CACHE=${STIC_APK_CACHE:-$WORK/stictest1/$GUEST/apk-cache}
# What in apk's log says the network was the trouble (apk-tools 3's fetch errors).
STIC_APK_NET='network error|DNS (lookup )?error|temporary error|timed out|timeout|Connection (refused|reset|timed out)|No route to host|Network is unreachable|TLS|unexpected end of file|Operation too slow'

# In the guest: /root/bko-apk is apk's cache dir; /root/apk.log its log;
# /root/apk.keep the cached .apk files of installed packages, once it worked.
STIC_APK_INNER='D=/root/bko-apk L=/root/apk.log
mkdir -p "$D"
for f in /var/cache/apk/APKINDEX.*.tar.gz; do
    [ -e "$f" ] && [ ! -e "$D/${f##*/}" ] && cp -p "$f" "$D/"
done
: >"$L"
i=0 rc=1
while [ $i -lt 4 ]; do
    i=$((i + 1))
    age=5256000
    [ $i -le 2 ] || age=0
    if [ $age = 0 ]; then w="index from the mirror"; else w="the image index"; fi
    echo "apk-add: try $i of 4 ($w)" >>"$L"
    apk add --no-interactive --no-progress --cache-dir "$D" --cache-packages --cache-max-age $age --timeout 120 $P >>"$L" 2>&1
    rc=$?
    [ $rc -eq 0 ] && break
    echo "apk-add: exit $rc" >>"$L"
    [ $i -lt 4 ] && sleep 20
done
tail -n 3 "$L"
[ $rc -eq 0 ] || exit $rc
apk info -v >/root/apk.inst 2>/dev/null
: >/root/apk.keep
for f in "$D"/*.apk; do
    [ -e "$f" ] || continue
    b=${f##*/}
    grep -qxF "${b%.*.apk}" /root/apk.inst && echo "$b" >>/root/apk.keep
done
exit 0'

stic_apk_add() {
    local pk="$*" c=$STIC_APK_CACHE t=${STIC_APK_T:-1800} rc have keep tmp n line
    if compgen -G "$c/*.apk" >/dev/null; then
        (cd "$c" && tar -cf - ./*.apk) | T=300 vm_ssh 'mkdir -p /root/bko-apk && tar -C /root/bko-apk -xf -' ||
            echo "apk-add: the host's cache didn't copy in: apk fetches what it needs"
    fi
    T=$t vm_sh "P='$pk'
$STIC_APK_INNER"
    rc=$?
    if [[ $rc -ne 0 ]]; then
        line=""
        if [[ $rc -eq 124 ]]; then
            line="no end after $t s"
        else
            line=$(T=60 vm_ssh 'cat /root/apk.log' 2>/dev/null | grep -E -i -m 1 "$STIC_APK_NET" | tr -s ' \t' ' ' | cut -c1-150)
        fi
        echo "FAIL harness-tools: apk add $pk failed${line:+ (waiting on the network: $line)}"
        return 1
    fi
    # Take back what apk kept when it differs from the host's cache (a package fetched anew).
    keep=$(T=60 vm_ssh 'cat /root/apk.keep' 2>/dev/null | sort)
    have=$(cd "$c" 2>/dev/null && ls -- *.apk 2>/dev/null | sort)
    [[ -n $keep && $keep != "$have" ]] || return 0
    mkdir -p "$(dirname "$c")" && tmp=$(mktemp -d "$c.new.XXXXXX") || return 0
    # shellcheck disable=SC2086 # file names, one word each
    if T=600 vm_ssh "cd /root/bko-apk && tar -cf - $(echo $keep)" | tar -C "$tmp" -xf - &&
        n=$(ls -- "$tmp" | wc -l) && n=$((n)) && [[ $n -eq $(wc -w <<<"$keep") ]]; then
        rm -rf -- "$c.old"
        [[ -d $c ]] && mv -- "$c" "$c.old"
        mv -- "$tmp" "$c" && rm -rf -- "$c.old"
        echo "apk-add: the host's cache has the $n package(s) installed from it now ($c)"
    else
        rm -rf -- "$tmp"
        echo "apk-add: the guest's packages didn't copy back: the host's cache stays as it was"
    fi
    return 0
}
