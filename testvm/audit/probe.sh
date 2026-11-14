#!/bin/sh
# In-guest: every executable name on the usual bin dirs, one "exe DIR NAME"
# line each, plus "os" lines. Reads only.
# No /dev/null: a chroot into a read-only vm-mount has an empty /dev.
PATH=/run/current-system/sw/bin:/run/current-system/sw/sbin:/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/gnu/bin:/opt/ooce/bin:/opt/ooce/sbin:/usr/xpg4/bin:/boot/system/bin:/boot/home/config/non-packaged/bin:/boot/system/non-packaged/bin:/usr/libexec:$PATH
export PATH
echo "os uname $(uname -srm)"
[ -r /etc/os-release ] && grep -E '^(ID|VERSION_ID)=' /etc/os-release | sed 's/^/os /'
seen=""
IFS=:
for d in $PATH; do
    [ -d "$d" ] || continue
    r=$(cd "$d" 2>&- && pwd -P) || continue
    case " $seen " in *" $r "*) continue ;; esac
    seen="$seen $r"
    for f in "$d"/*; do
        [ -x "$f" ] && [ ! -d "$f" ] && echo "exe $d ${f##*/}"
    done
done
unset IFS
echo "end"
