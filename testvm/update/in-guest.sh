#!/bin/sh
# Run as root (Haiku: its only user, uid 0) inside a BackOtter test guest
# by vm-update (ssh ... sh -s):
# bring the guest up to date with its own package manager, unattended, then
# empty the package caches.  Exits 0 when the update ran, 3 when nothing on
# this guest can update it, anything else when the update failed.
#
# Several builders freeze their guest's repositories at the day or release
# it was built (Ubuntu, Debian, Arch, Fedora, Rocky, Tumbleweed), so that the
# install is reproducible; updating such a guest would change nothing.  So
# the first update points it at its distribution's live repositories, where
# it stays.

PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export PATH
has() { command -v "$1" >/dev/null 2>&1; }
say() { echo "in-guest: $*"; }
say "$(uname -srm)"

if [ -e /etc/NIXOS ]; then
    # Built from a pinned release tarball (nix.nixPath): move it to the
    # newest of the same release's channel, and rebuild from that.
    release=$(. /etc/os-release; echo "$VERSION_ID")
    url=https://channels.nixos.org/nixos-$release/nixexprs.tar.xz
    say "nixos-rebuild from $url"
    # nix.nixPath holds only nixpkgs=, so name the configuration too (in the
    # guest's own path as well, for its later nixos-rebuild runs).
    sed -i "s|\"nixpkgs=[^\"]*\"|\"nixpkgs=$url\"|" /etc/nixos/configuration.nix
    grep -q 'nixos-config=' /etc/nixos/configuration.nix ||
        sed -i "s|\"nixpkgs=$url\"|& \"nixos-config=/etc/nixos/configuration.nix\"|" /etc/nixos/configuration.nix
    nixos-rebuild switch -I "nixpkgs=$url" -I nixos-config=/etc/nixos/configuration.nix || exit 1
    # Every generation is a GC root and keeps its kernel in the 1 GiB /boot:
    # keep this one and the one before, drop what only the others held, and
    # rewrite the boot menu, which removes their kernels from /boot.
    nix-env -p /nix/var/nix/profiles/system --delete-generations +2 &&
        nix-collect-garbage && /nix/var/nix/profiles/system/bin/switch-to-configuration boot || exit 1
    say "updated"
    exit 0
fi

# --- Unfreeze -----------------------------------------------------------------------
# Each pin is recognized by its own mark, so an unfrozen guest is left alone.
if [ "$(uname)" = Linux ]; then
    . /etc/os-release
    f=/etc/apt/sources.list.d/ubuntu.sources
    if [ -f $f ] && grep -q 'snapshot\.ubuntu\.com' $f; then
        # The archive serves the -security suites too.
        say "unfreezing: $f from snapshot.ubuntu.com to archive.ubuntu.com"
        sed -i -E 's#https://snapshot\.ubuntu\.com/ubuntu/[^/ ]+/?#http://archive.ubuntu.com/ubuntu/#' $f
    fi
    if [ "$ID" = debian ] && has apt-get &&
        ! grep -hqsE '^[[:space:]]*(deb|URIs:)[[:space:]]' /etc/apt/sources.list /etc/apt/sources.list.d/*; then
        # Installed from the DVD alone, which the installer then disabled.
        f=/etc/apt/sources.list.d/debian.sources
        say "unfreezing: $f, Debian's mirror and security archive for $VERSION_CODENAME"
        cat >$f <<EOF
Types: deb
URIs: http://deb.debian.org/debian
Suites: $VERSION_CODENAME $VERSION_CODENAME-updates
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $VERSION_CODENAME-security
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
    fi
    f=/etc/pacman.d/mirrorlist
    if [ -f $f ] && grep -q 'archive\.archlinux\.org/repos/' $f; then
        say "unfreezing: $f from Arch's archive to its mirrors"
        echo 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch' >$f
    fi
    if grep -qs '^ID=artix' /etc/os-release && [ -f $f ] && ! grep -q '^Server = .*artix' $f; then
        # Artix's builder once let pacstrap copy the Arch live system's
        # mirror list in (no -M), whose mirrors serve no Artix repository.
        # Artix's own, the file as its artix-mirrorlist package has it,
        # fetched through the mirror the guest was built from.
        say "unfreezing: $f, Artix's mirrors instead of Arch's"
        echo 'Server = https://mirrors.dotsrc.org/artix-linux/repos/$repo/os/$arch' >$f
        pkg=/var/cache/pacman/pkg/$(pacman -Sy >/dev/null && pacman -Sp artix-mirrorlist | sed 's|.*/||')
        pacman -Sw --noconfirm artix-mirrorlist && bsdtar -xOf "$pkg" etc/pacman.d/mirrorlist >$f.new &&
            mv $f.new $f || exit 1
        grep -q '^Server = .*artix' $f || { say "artix-mirrorlist gave no Artix mirrors"; exit 1; }
    fi
    f=/etc/yum.repos.d/fedora-updates.repo
    if [ -f $f ] && sed -n '/^\[updates\]/,/^\[/p' $f | grep -q '^enabled=0'; then
        say "unfreezing: $f, the updates repository on again"
        sed -i '/^\[updates\]/,/^\[/ s/^enabled=0/enabled=1/' $f
    fi
    if grep -qs '^baseurl=https://dl\.rockylinux\.org/vault/rocky/' /etc/yum.repos.d/rocky*.repo; then
        say "unfreezing: Rocky's repositories from the vault to the mirrors"
        sed -i -e 's/^#mirrorlist=/mirrorlist=/' \
            -e 's|^baseurl=https://dl\.rockylinux\.org/vault/rocky/[^/]*/|#baseurl=http://dl.rockylinux.org/$contentdir/$releasever/|' \
            /etc/yum.repos.d/rocky*.repo
    fi
    if grep -qs '^baseurl=https\?://download\.opensuse\.org/history/' /etc/zypp/repos.d/*.repo; then
        say "unfreezing: Tumbleweed's repositories from openSUSE's history to the live ones"
        sed -i -E 's#^baseurl=https?://download\.opensuse\.org/history/[^/]+/#baseurl=http://download.opensuse.org/#' \
            /etc/zypp/repos.d/*.repo
    fi
    # The DVD it was installed from, which YaST adds as a repository after
    # the profile's own scripts have run: no DVD here, and zypper fails on
    # a repository it can't reach.
    for f in $(grep -lsE '^baseurl=(cd|dvd):' /etc/zypp/repos.d/*.repo); do
        say "dropping the install DVD's repository ($(basename "$f"))"
        rm -f "$f"
    done
fi

# --- Room ---------------------------------------------------------------------------
# NILFS2 (zorin18's root) keeps what is deleted or overwritten, in its
# checkpoints, until its cleaner reclaims it, an hour later by default: an
# update would fill the disk with the files it replaced.  So reclaim runs the
# cleaner at once (no protection period) and waits for it.  But only while
# nothing else writes: the cleaner running beside a big apt run broke the
# file system (a bad DAT b-tree node, then read-only).  In between, it's
# suspended.
nilfs=
reclaim() { :; }
if [ "$(uname)" = Linux ] && [ "$(findmnt -no FSTYPE / 2>/dev/null)" = nilfs2 ] && has nilfs-clean; then
    nilfs=$(findmnt -no SOURCE /)
    reclaim() {
        sync
        nilfs-clean -r "$nilfs" && nilfs-clean -p 0 "$nilfs" || { say "nilfs-clean failed"; exit 1; }
        # Until 10 s free up less than 64 MiB, after 30 s at least (the
        # oldest segments, cleaned first, may hold little to free), and for
        # 5 minutes at most.
        now=$(df -Pk / | awk 'NR == 2 { print $4 }') i=0
        while [ $i -lt 30 ]; do
            sleep 10; i=$((i + 1))
            prev=$now now=$(df -Pk / | awk 'NR == 2 { print $4 }')
            [ $i -lt 3 ] || [ $((now - prev)) -ge 65536 ] || break
        done
        nilfs-clean -s "$nilfs" || { say "could not suspend NILFS's cleaner"; exit 1; }
        nilfs_ok
        say "NILFS: $((now / 1024)) MiB free"
    }
    nilfs_ok() {
        if dmesg | grep -q 'NILFS error'; then
            say "NILFS reported errors:"; dmesg | grep 'NILFS' | tail -5; exit 1
        fi
    }
    reclaim
fi

case $(uname) in
Linux)
    if has apt-get; then
        export DEBIAN_FRONTEND=noninteractive
        # The guest's own apt jobs (apt-daily, unattended-upgrades, PackageKit)
        # often start at boot, and would take apt's locks between our steps:
        # stop them for this boot (runtime state only, in /run).  Then wait
        # for any still holding a lock: DPkg::Lock::Timeout covers dpkg's
        # lock, not the lists'.
        if [ -d /run/systemd/system ]; then
            systemctl mask --runtime --now packagekit.service >/dev/null 2>&1
            systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service \
                >/dev/null 2>&1
        fi
        # Error-Mode=any: a repository that failed to download is an error
        # (to retry), not a warning that leaves the old lists in place.
        apt="apt-get -y -o DPkg::Lock::Timeout=900 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
        tries=0
        until $apt -o APT::Update::Error-Mode=any update; do
            tries=$((tries + 1)); [ $tries -lt 60 ] || exit 1; sleep 15
        done
        # In batches, the downloads deleted after each: a desktop guest's
        # 16 GB disk hasn't room for all of a big update's packages at once
        # beside their unpacked files.  dist-upgrade does the rest (packages
        # that replace others).
        # snapd's postinst sets file capabilities, which NILFS2 can't store
        # (make-from-live.sh unpacked the system without them): there it
        # stays as built.
        if [ -n "$nilfs" ] && dpkg -s snapd >/dev/null 2>&1; then
            apt-mark hold snapd >/dev/null || exit 1
        fi
        apt-mark showhold >/run/bko-held || exit 1
        set -- $(apt list --upgradable 2>/dev/null | sed -n 's|/.*||p' | grep -vxF -f /run/bko-held)
        while [ $# -gt 0 ]; do
            batch=
            while [ $# -gt 0 ] && [ $(echo $batch | wc -w) -lt 40 ]; do batch="$batch $1"; shift; done
            $apt install --only-upgrade $batch && $apt clean || exit 1
            reclaim
        done
        $apt dist-upgrade && $apt autoremove --purge && $apt clean || exit 1
    elif has dnf; then
        dnf -y upgrade --refresh && dnf clean all || exit 1
    elif has pacman; then
        # The keyrings first: packages signed by keys newer than the guest's
        # keyring would fail to verify.  Omarchy's pacman hook stops a plain
        # pacman upgrade (for "omarchy update", which is interactive) unless
        # told this is meant.
        export OMARCHY_ALLOW_DIRECT_PACMAN=1
        keyrings=$(pacman -Qq archlinux-keyring artix-keyring cachyos-keyring omarchy-keyring 2>/dev/null)
        pacman -Sy --noconfirm --needed $keyrings && pacman -Su --noconfirm || exit 1
        yes | pacman -Scc >/dev/null
    elif has zypper; then
        # 103: zypper updated itself and wants to run again.  100-102: done,
        # with something to say (a reboot is needed, for one).
        for i in 1 2 3; do
            zypper -n --gpg-auto-import-keys dup --auto-agree-with-licenses; rc=$?
            [ $rc -eq 103 ] || break
        done
        [ $rc -eq 0 ] || [ $rc -ge 100 -a $rc -le 102 ] || exit 1
        zypper -n clean -a
    elif has xbps-install; then
        # xbps itself first, as xbps-install asks.
        xbps-install -Syu xbps || [ $? -eq 17 ] || exit 1   # 17: it is current already
        xbps-install -yu || exit 1
        # Kernels are preserve=yes: an upgrade keeps the old one, its
        # initramfs and modules, until vkpurge removes them (not the one
        # running, nor any a package still owns).
        if has vkpurge; then vkpurge rm all || exit 1; fi
        xbps-remove -yO || exit 1
    elif has apk; then
        apk upgrade -U || exit 1
    else
        say "no package manager found"; exit 3
    fi ;;
FreeBSD)
    export ASSUME_ALWAYS_YES=yes
    if pkg info -e FreeBSD-runtime; then
        # pkgbase: the base system is packages too, upgraded with the rest.
        pkg update -f && pkg upgrade -y || exit 1
    else
        PAGER=cat freebsd-update --not-running-from-cron fetch install; rc=$?
        [ $rc -eq 0 ] || [ $rc -eq 2 ] || exit 1      # 2: nothing to install
        pkg upgrade -y || exit 1
    fi
    pkg clean -ay ;;
DragonFly)
    # Packages only: DragonFly's base system is updated from source.
    export ASSUME_ALWAYS_YES=yes
    pkg upgrade -y && pkg clean -ay || exit 1 ;;
OpenBSD)
    # Not while the kernel is relinked (KARL), as every boot does, starting
    # late: two relinks at once break each other.  So wait for 30 s with
    # none, patch, and then relink again until it works (syspatch's own
    # relink fails when it collides with one, and the first run after a
    # failed one fails too).
    quiet() {
        n=0 t=0
        while [ $n -lt 3 ]; do
            if pgrep -qf reorder_kernel; then n=0; else n=$((n + 1)); fi
            t=$((t + 1)); [ $t -lt 180 ] || { say "reorder_kernel never ended"; return 1; }
            sleep 10
        done
    }
    quiet || exit 1
    # syspatch updates itself first when it has to, and must then run again
    # (exit 2).  Exit 1 also when only its relink failed: retried below.
    for i in 1 2 3; do
        missing=$(syspatch -c) || { say "syspatch -c failed"; exit 1; }
        [ -n "$missing" ] || break
        syspatch; [ $? -le 2 ] || exit 1
    done
    missing=$(syspatch -c) || { say "syspatch -c failed"; exit 1; }
    [ -z "$missing" ] || { say "patches still missing:" $missing; exit 1; }
    for i in 1 2 3; do
        quiet || exit 1
        /usr/libexec/reorder_kernel && break
        [ $i -lt 3 ] || { say "the kernel relink keeps failing"; exit 1; }
    done
    pkg_add -u -I || exit 1 ;;
NetBSD)
    if has apk; then
        # AlpineBSD.  Its install has no CA certificates to check TLS with;
        # the packages' signatures are still checked against its key.
        # openssh-sftp-server: the sftp-server dropbear runs for scp and
        # sftp, which a guest built before netbsd/install.sh added it lacks.
        apk add -U --check-certificate=no openssh-sftp-server || exit 1
        apk upgrade --check-certificate=no || exit 1
    else
        # NetBSD, installed from a release's base sets.  netbsd/install.sh
        # now adds what updates it; for a guest built before that, the
        # first update does: the CA certificates its base set ships
        # (certctl); IPv4 first (a network that hands out an IPv6 address
        # leading nowhere, as QEMU's user network did before vm-run turned
        # its IPv6 off, stalls a download); pkgin, from pkgsrc's binary
        # packages for this release; and sysupgrade.  Then the base system
        # from the release branch's newest build (security fixes within
        # days, though with SHA512 sums only, unsigned: fetched over TLS
        # from NetBSD's CDN), and the packages.  etcupdate, which asks
        # about every changed file, stays out: postinstall fixes what a
        # newer base needs of /etc (obsolete files, mtree's lists, the
        # certificates' hashes).
        arch=$(uname -m) rel=$(uname -r | sed 's/_.*//')
        # The running kernel's configuration, "(GENERIC)" in uname -v:
        # KERNEL=AUTO would need config(1), from the comp set, not here.
        kernel=$(uname -v | sed -n 's/.*(\([A-Za-z0-9_]*\)).*/\1/p')
        [ -n "$(ls /etc/openssl/certs)" ] || certctl rehash || exit 1
        if ! grep -q '^ip6addrctl_policy=ipv4_prefer' /etc/rc.conf; then
            printf 'ip6addrctl=YES\nip6addrctl_policy=ipv4_prefer\n' >>/etc/rc.conf
        fi
        /etc/rc.d/ip6addrctl start >/dev/null || exit 1
        pkgs=https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/$arch/$rel/All
        if ! has pkgin; then
            say "installing pkgin and sysupgrade from $pkgs"
            PKG_PATH=$pkgs pkg_add pkgin || exit 1
            echo "$pkgs" >/usr/pkg/etc/pkgin/repositories.conf
        fi
        has sysupgrade || pkgin -y install sysupgrade || exit 1
        cat >/usr/pkg/etc/sysupgrade.conf <<EOF
RELEASEDIR="https://nycdn.netbsd.org/pub/NetBSD-daily/netbsd-${rel%%.*}/latest/$arch"
KERNEL=${kernel:-GENERIC}
SETS=AUTO
POSTINSTALL_AUTOFIX="obsolete mtree opensslcertsrehash"
ETCUPDATE=no
AUTOCLEAN=yes
EOF
        sysupgrade auto || exit 1
        # The sshd still running hands each new connection to the binaries
        # just replaced, which needn't match it: the new one takes over.
        # (Its pid file comes a moment after start returns.)
        /etc/rc.d/sshd restart >/dev/null || exit 1
        n=0; until /etc/rc.d/sshd status >/dev/null 2>&1; do
            n=$((n + 1)); [ $n -lt 15 ] || { say "sshd didn't come back"; exit 1; }; sleep 1
        done
        pkgin -y update && pkgin -y upgrade && pkgin -y clean || exit 1
    fi ;;
SunOS)
    # A new boot environment when the update needs one; the reboot after
    # this activates it.  4: nothing to update.
    pkg update -v; rc=$?
    [ $rc -eq 0 ] || [ $rc -eq 4 ] || exit 1 ;;
Haiku)
    # Each update keeps the packages it replaced in a state folder, to boot
    # back into from the boot menu: the last update's go first, as the
    # running system doesn't use them.  Updates to packages in use (haiku,
    # the kernel's) take effect at the reboot after this.
    rm -rf /boot/system/packages/administrative/state_* || exit 1
    # A repository pkgman can't refresh is only a warning, and the sync
    # then goes by the old index ("Nothing to do", exit 0): an error here,
    # retried while the network may still be coming up.
    unrefreshed() { grep -Eq 'Refreshing repository .* failed|Skipping\.' /tmp/bko-pkgman.log; }
    tries=0
    until pkgman refresh >/tmp/bko-pkgman.log 2>&1 && ! unrefreshed; do
        tries=$((tries + 1))
        [ $tries -lt 20 ] || { cat /tmp/bko-pkgman.log; say "the repositories can't be refreshed"; exit 1; }
        sleep 15
    done
    pkgman full-sync -y >/tmp/bko-pkgman.log 2>&1; rc=$?
    cat /tmp/bko-pkgman.log
    [ $rc -eq 0 ] || exit 1
    ! unrefreshed || { say "a repository couldn't be refreshed"; exit 1; } ;;
*)
    say "no updater for $(uname)"; exit 3 ;;
esac
if [ -n "$nilfs" ]; then
    nilfs_ok
    nilfs-clean -r "$nilfs" || exit 1
fi
say "updated"
