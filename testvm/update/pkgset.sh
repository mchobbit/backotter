#!/bin/sh
# Run inside a BackOtter test guest by vm-update (ssh ... sh -s), before
# update/in-guest.sh and again after the reboot: print what the guest has
# installed, one typed line each.
#
#   pkg NAME VERSION [ARCH]    a package of its package manager (Portage:
#                              the CHOST for ARCH)
#   nix-pkg NAME VERSION       a store path in NixOS's system closure
#   nix-system PATH            NixOS's system profile
#   syspatch ID                an OpenBSD patch applied
#   base NAME VALUE            what no package manager lists: the base system
#                              of a BSD (a kernel's or libc's sha256, a release
#                              string), and "base pm KIND", the kind of listing
#   unknown [WHY]              alone: this guest can't be listed
#
# vm-update compares the two listings (the merge rule: anything that differs
# means a merge), and update/fingerprint.py reads the second.  So a listing
# that isn't whole says unknown instead: a part of one could hide a change.
# Each listing only reads; pkg -N first, as any other pkg command installs
# pkg on a guest that has none.

PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/sbin:/usr/bin:/sbin:/bin:/run/current-system/sw/bin:$PATH
LC_ALL=C
export PATH LC_ALL
has() { command -v "$1" >/dev/null 2>&1; }
# The whole listing is captured, so what stops it is its last line.
fail() { echo "unknown $*"; exit 1; }
# Each line of $1, or none (printf would make one of an empty listing).
lines() { [ -z "$1" ] || printf '%s\n' "$1"; }

# name-version as "pkg NAME VERSION": the version starts at the first
# "-DIGIT" (pkg_info's and pkgsrc's names).
pkg_dash() { awk '{ i = match($0, /-[0-9]/); if (i) print "pkg", substr($0, 1, i - 1), substr($0, i + 1); else print "pkg", $0, "-" }'; }

pkg_apk() {
    x=$(apk info -v 2>/dev/null) || fail "apk info -v failed"
    lines "$x" | sed 's/^\(.*\)-\([^-]*-r[0-9]*\)$/pkg \1 \2/'
}
# Portage's database of what is installed: /var/db/pkg/CATEGORY/PF, PF
# being NAME-VERSION[-rN], with its CHOST in a file of that name.  The bare
# NAME, as bko-holt's dump has it (docs/design/gentoo.md, decision 4).  A
# merge under way (CATEGORY/-MERGING-PF) is a listing that isn't whole.
portage_dirs() {
    for d in /var/db/pkg/*/*; do
        [ -d "$d" ] || continue
        case ${d##*/} in
        -MERGING-*) echo "-MERGING- ${d#/var/db/pkg/}" ;;
        *) echo "${d##*/} $(cat "$d/CHOST" 2>/dev/null)" ;;
        esac
    done
}
pkg_portage() {
    x=$(portage_dirs)
    [ -n "$x" ] || fail "/var/db/pkg lists nothing"
    lines "$x" | awk '$1 == "-MERGING-" { print "unknown a merge under way:", $2; exit 1 }
                      { i = match($1, /-[0-9]+(\.[0-9]+)*[a-z]?(_(alpha|beta|pre|rc|p)[0-9]*)*(-r[0-9]+)?$/)
                        if (!i) { print "unknown a package name without a version:", $1; exit 1 }
                        print "pkg", substr($1, 1, i - 1), substr($1, i + 1), $2 }'
}
pkg_query() {
    x=$(pkg query '%n %v') || fail "pkg query failed"
    lines "$x" | sed 's/^/pkg /'
}

listing() {
    if [ -e /etc/NIXOS ]; then
        echo "base pm nixos"
        sys=$(readlink -f /nix/var/nix/profiles/system)
        [ -n "$sys" ] || fail "no NixOS system profile"
        echo "nix-system $sys"
        x=$(nix-store -qR "$sys") || fail "nix-store -qR failed"
        # /nix/store/HASH-NAME-VERSION[-OUTPUT]
        lines "$x" | awk '{ s = $0; sub(/^\/nix\/store\/[^-]*-/, "", s); i = match(s, /-[0-9]/)
                            if (i) { v = substr(s, i + 1); sub(/-.*/, "", v); print "nix-pkg", substr(s, 1, i - 1), v } }' | sort -u
        return
    fi
    case $(uname) in
    Linux)
        # Portage first, as in-guest.sh does: a Gentoo may have dpkg or rpm too.
        if [ -e /etc/gentoo-release ] && [ -d /var/db/pkg ]; then
            echo "base pm portage"
            pkg_portage
        elif has apt-get && has dpkg; then
            # (BigLinux's apt-get and dnf are scripts that only exec pamac;
            # its rpm is real, with an empty database.)
            echo "base pm dpkg"
            x=$(dpkg-query -W -f '${db:Status-Abbrev} ${Package} ${Version} ${Architecture}\n') || fail "dpkg-query failed"
            lines "$x" | awk '$1 ~ /^.i/ { print "pkg", $2, $3, $4 }'
        elif { has dnf && ! grep -qs '^exec pamac' "$(command -v dnf)"; } || { ! has pacman && has zypper; }; then
            echo "base pm rpm"
            x=$(rpm -qa --qf '%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n') || fail "rpm -qa failed"
            lines "$x" | sed 's/^/pkg /'
        elif has pacman; then
            echo "base pm pacman"
            x=$(pacman -Q) || fail "pacman -Q failed"
            lines "$x" | sed 's/^/pkg /'
        elif has xbps-install; then
            echo "base pm xbps"
            x=$(xbps-query -l) || fail "xbps-query -l failed"
            lines "$x" | awk '{ print $2 }' | sed 's/^\(.*\)-\([^-]*\)$/pkg \1 \2/'
        elif has apk; then
            echo "base pm apk"
            pkg_apk
        elif has moss; then
            echo "base pm moss"
            # moss lists under `pkg`: v0.26.10 (64070b2 and 05bd904) has no
            # `list`, so `moss list installed` is an unknown subcommand
            # (exit status 2).
            x=$(moss pkg list --installed) || fail "moss pkg list --installed failed (exit status $?)"
            # One a line, NAME VERSION-RELEASE - SUMMARY (styled only on a
            # tty).  Any other line, or none at all: not a whole listing.
            lines "$x" | awk '$2 ~ /[0-9]/ && $3 == "-" { n++; print "pkg", $1, $2; next }
                              { bad = 1; print "unknown moss pkg list --installed printed:", substr($0, 1, 60); exit 1 }
                              END { if (!bad && !n) { print "unknown moss pkg list --installed listed nothing"; exit 1 } }'
        else
            fail "no package manager found"
        fi ;;
    FreeBSD)
        if has hbsd-update; then echo "base pm hardenedbsd"; else echo "base pm freebsd"; fi
        x=$(sha256 -q /boot/kernel/kernel) || fail "no sha256 of the kernel"
        echo "base kernel $x"
        x=$(sha256 -q /lib/libc.so.7) || fail "no sha256 of libc"
        echo "base libc $x"
        x=$(freebsd-version -k) || fail "freebsd-version -k failed"
        echo "base freebsd-version-k $x"
        x=$(freebsd-version -u) || fail "freebsd-version -u failed"
        echo "base freebsd-version-u $x"
        if [ -f /var/db/hbsd-update/version ]; then echo "base hbsd-version $(cat /var/db/hbsd-update/version)"; fi
        if has zfs; then
            x=$(zfs version 2>/dev/null | sed -n '1s/^zfs-//p')
            if [ -n "$x" ]; then echo "base zfs $x"; fi
        fi
        if pkg -N >/dev/null 2>&1; then pkg_query; fi ;;
    DragonFly)
        echo "base pm dragonfly"
        # The commit in-guest.sh last rebuilt the base from; none before the first.
        if [ -f /var/db/bko-base ]; then echo "base bko-base $(cat /var/db/bko-base)"; fi
        if pkg -N >/dev/null 2>&1; then pkg_query; fi ;;
    OpenBSD)
        echo "base pm openbsd"
        # /bsd itself says nothing: KARL relinks it at every boot.
        x=$(syspatch -l) || fail "syspatch -l failed"
        lines "$x" | sed 's/^/syspatch /'
        echo "base syspatch-count $(lines "$x" | wc -l | tr -d ' ')"
        x=$(pkg_info -q) || fail "pkg_info -q failed"
        lines "$x" | pkg_dash ;;
    NetBSD)
        if has apk; then
            echo "base pm alpinebsd"
            pkg_apk
        else
            echo "base pm netbsd"
            f=/var/db/bko-netbsd-daily.sha512
            if [ -f $f ]; then
                x=$(cksum -a sha512 $f | awk '{ print $NF }')
                [ -n "$x" ] || fail "no sha512 of $f"
                echo "base netbsd-daily $x"
            fi
            x=$(pkg_info -q) || fail "pkg_info -q failed"
            lines "$x" | pkg_dash
        fi ;;
    SunOS)
        echo "base pm illumos"
        x=$(pkg list -H) || fail "pkg list -H failed"
        # NAME [(PUBLISHER)] VERSION IFO
        lines "$x" | awk '{ if ($2 ~ /^\(/) print "pkg", $1, $3; else print "pkg", $1, $2 }' ;;
    Haiku)
        echo "base pm haiku"
        x=$(ls /boot/system/packages/*.hpkg) || fail "no packages in /boot/system/packages"
        lines "$x" | sed 's|.*/||; s/\.hpkg$//; s/^\([^-]*\)-\(.*\)$/pkg \1 \2/' ;;
    *)
        fail "no listing for $(uname)" ;;
    esac
}

out=$(listing)
rc=$?
if [ $rc -ne 0 ]; then
    printf '%s\n' "$out" | sed -n '$p'
elif [ -z "$out" ]; then
    echo "unknown the listing was empty"
else
    printf '%s\n' "$out"
fi
exit 0
