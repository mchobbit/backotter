#!/bin/sh
# Run as root (Haiku: its only user, uid 0) inside a BackOtter test guest
# by vm-update (ssh ... sh -s):
# bring the guest up to date with its own package manager, unattended, then
# empty the package caches.  Exits 0 when the update ran, 3 when nothing on
# this guest can update it, anything else when the update failed.
#
# Several builders freeze their guest's repositories at the day or release
# it was built (Ubuntu, Debian, Arch, Fedora, Rocky, Tumbleweed, AerynOS),
# so that the install is reproducible; updating such a guest would change
# nothing.  So the first update points it at its distribution's live
# repositories, where it stays.
#
# "in-guest: step: WHAT" lines name what this run changed besides packages:
# vm-update's merge rule reads them, with update/pkgset.sh's listing before and
# after.  So each fires only when it changed something: a step on every run
# would merge every run.  The "# t2-section: NAME" lines mark what each kind of
# guest runs, for GuestTest2's keys (testvm/test2/README.md): a change in one
# section re-tests only its guests.  From one marker to the next is that name's.
# A section holds only code its guests alone run: what every guest that gets
# there tests (an if or elif condition, a case pattern, a for list, the fi,
# done or ;; that ends it) is the prologue's, or Linux.common's inside Linux,
# and so is code a guest of any package manager may run (the file system
# fixes).  test_vm_update checks that each stretch of a guest's own section
# parses alone, between such a test and its end.

# t2-section: prologue
PATH=/usr/local/sbin:/usr/local/bin:/usr/pkg/sbin:/usr/pkg/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export PATH
has() { command -v "$1" >/dev/null 2>&1; }
say() { echo "in-guest: $*"; }
step() { echo "in-guest: step: $*"; }
say "$(uname -srm)"

if [ -e /etc/NIXOS ]; then
    # t2-section: NixOS
    # Built from a pinned release tarball (nix.nixPath): move it to the
    # newest of the same release's channel, and rebuild from that.
    release=$(. /etc/os-release; echo "$VERSION_ID")
    url=https://channels.nixos.org/nixos-$release/nixexprs.tar.xz
    say "nixos-rebuild from $url"
    # nix.nixPath holds only nixpkgs=, so name the configuration too (in the
    # guest's own path as well, for its later nixos-rebuild runs).
    if ! grep -qF "\"nixpkgs=$url\"" /etc/nixos/configuration.nix; then
        sed -i "s|\"nixpkgs=[^\"]*\"|\"nixpkgs=$url\"|" /etc/nixos/configuration.nix
        step "configuration.nix: nixpkgs is $url"
    fi
    if ! grep -q 'nixos-config=' /etc/nixos/configuration.nix; then
        sed -i "s|\"nixpkgs=$url\"|& \"nixos-config=/etc/nixos/configuration.nix\"|" /etc/nixos/configuration.nix
        step "configuration.nix: nixos-config named in nixPath"
    fi
    nixos-rebuild switch -I "nixpkgs=$url" -I nixos-config=/etc/nixos/configuration.nix || exit 1
    # Every generation is a GC root and keeps its kernel in the 1 GiB /boot:
    # keep this one and the one before, drop what only the others held, and
    # rewrite the boot menu, which removes their kernels from /boot.
    nix-env -p /nix/var/nix/profiles/system --delete-generations +2 &&
        nix-collect-garbage && /nix/var/nix/profiles/system/bin/switch-to-configuration boot || exit 1
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Guix
# Guix System (make-guix.sh: guix15, guix15-bios): built from the 1.5.0
# installation image's Guix; root's `guix pull` follows the channel's head
# (docs/design/guix.md, decision 6), and the system is rebuilt from the
# guest's own /etc/config.scm.  Its tools are only in the profiles.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ -d /gnu/store ] && [ -d /var/guix/profiles ] && [ -f /etc/config.scm ]; then
    # t2-section: Guix
    PATH=/root/.config/guix/current/bin:/run/current-system/profile/bin:/run/current-system/profile/sbin:$PATH
    export PATH
    guix pull || exit 1
    say "$(guix describe | head -5 | tr '\n' ' ')"
    # --allow-downgrades: 1.5.0's commit (d58da8a, the installed system's)
    # is on its release branch, not an ancestor of master's, so the first
    # reconfigure would abort as a "downgrade" (2026-11-14).  The pull above
    # just took the channel's head, so nothing older can come in this way.
    guix system reconfigure --allow-downgrades /etc/config.scm || exit 1
    # Every generation is a GC root: keep this one and the one before
    # (delete-generations rewrites grub.cfg's menu), drop the older Guix
    # builds of root's pulls, then what only those held.
    old=$(ls /var/guix/profiles | sed -n 's/^system-\([0-9][0-9]*\)-link$/\1/p' | sort -n | head -n -2 | paste -sd, -)
    if [ -n "$old" ]; then guix system delete-generations "$old" || exit 1; fi
    guix package -p /var/guix/profiles/per-user/root/current-guix --delete-generations || exit 1
    guix gc || exit 1
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Linux.portage
# Gentoo (testvm/bootstrap/gentoo.sh: gentoo, gentoo-systemd): Portage, with binary packages from
# the 23.0 binhost (make.conf's getbinpkg, signatures checked against
# getuto's keyring).  Before the other Linux package managers, as NixOS is:
# a Gentoo may have dpkg, rpm or dnf emerged as tools.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ -e /etc/gentoo-release ] && has emerge; then
    # t2-section: Linux.portage
    # The only source builds allowed: what the binhost has no build of with
    # the guest's USE flags, bootstrap/gentoo.sh's SOURCE_OK
    # (test2/tests/test_vm_update.py checks that the two agree).  Any other
    # "[ebuild" line in the plan stops the update, by name, before anything
    # is built.  make.conf's getbinpkg makes the plan's -puDN the -uDNg after.
    SOURCE_OK="sys-fs/lvm2 sys-boot/grub sys-kernel/dracut sys-kernel/installkernel sys-kernel/gentoo-kernel-bin virtual/dist-kernel net-dns/avahi app-doc/info-manual sys-apps/systemd net-libs/nghttp2 net-misc/rsync dev-python/charset-normalizer"
    o="--color=n --nospinner"
    # Again, up to 5 times a minute apart, when the sync fails: an rsync
    # mirror caught mid-update fails the tree's Manifest check
    # ("Manifest mismatch for metadata/Manifest.gz", gentoo-systemd
    # 2026-11-17), and the next sync puts it right.
    tries=0
    until emerge --sync -q $o; do
        tries=$((tries + 1)); [ $tries -lt 5 ] || exit 1
        say "emerge --sync failed: again in 60 s ($tries of 4)"; sleep 60
    done
    plan=$(emerge -puDN $o @world) || { printf '%s\n' "$plan"; say "emerge -puDN @world failed"; exit 1; }
    printf '%s\n' "$plan"
    # CATEGORY/NAME of each "[ebuild" line: the version is the last
    # -VERSION[-rN] of the name.
    bad=$(printf '%s\n' "$plan" | sed -n 's/^\[ebuild[^]]*\] *\([^ ]*\).*/\1/p' |
        sed -E 's/:.*//; s/-[0-9]+(\.[0-9]+)*[a-z]?(_(alpha|beta|pre|rc|p)[0-9]*)*(-r[0-9]+)?$//' |
        grep -vxF "$(printf '%s\n' $SOURCE_OK)")
    [ -z "$bad" ] || { say "source builds outside the allowlist:" $bad; exit 1; }
    # sys-boot/grub's COUNTER: Portage's merge counter, new on every merge of it.
    grubc() { cat /var/db/pkg/sys-boot/grub-[0-9]*/COUNTER 2>/dev/null; }
    grub_was=$(grubc)
    emerge -uDNg $o --quiet-build=y @world && emerge --depclean $o || exit 1
    # CONFIG_PROTECT's ._cfgNNNN_NAME files: a package's new default for a
    # config file the image changed (Portage replaces one the image left as
    # it was).  The image's file stays, as apt's --force-confold keeps it,
    # when it still has every line of the new default (the default plus the
    # image's additions: bootstrap/gentoo.sh appends GRUB_CMDLINE_LINUX and
    # GRUB_TIMEOUT to /etc/default/grub, and grub 2.14-r5 and 2.16-r1 both
    # install the same files/grub.default-4, without them).  Removing the
    # ._cfg file is etc-update's "keep": Portage's confmem then skips that
    # same default from now on.  Any other is left for review, and named.
    for c in $(find $(portageq envvar CONFIG_PROTECT) -name '._cfg[0-9][0-9][0-9][0-9]_*' 2>/dev/null | sort); do
        f=${c%/*}/${c##*/._cfg[0-9][0-9][0-9][0-9]_}
        if [ -f "$f" ] && ! grep -vxF -f "$f" -- "$c" >/dev/null; then
            rm -f -- "$c" && say "kept the image's $f: it has every line of $c"
        else
            say "config file left for review: $c"
        fi
    done
    # GRUB merged again: its ebuild only says "Re-run grub-install ... Re-run
    # grub-mkconfig", so both are done here, as bootstrap/gentoo.sh installed
    # it: UEFI only (GRUB_PLATFORMS="efi-64": no BIOS boot code), under the
    # id gentoo with no NVRAM entry, and again at the fallback path
    # EFI/BOOT/BOOTX64.EFI that the VM's firmware boots; the ESP at /efi
    # (gentoo) or /boot (gentoo-systemd).  Else the old GRUB (ESP image and
    # /boot/grub's modules) would boot a grub.cfg the new grub-mkconfig writes
    # at the next kernel's installkernel.
    if [ -n "$(grubc)" ] && [ "$(grubc)" != "$grub_was" ]; then
        g=$(ls -d /var/db/pkg/sys-boot/grub-[0-9]*); g=${g#/var/db/pkg/}
        esp=""
        for e in /efi /boot /boot/efi; do
            [ -e "$e/EFI/gentoo/grubx64.efi" ] && grep -q "^[^ ]* $e vfat " /proc/mounts || continue
            grub-install --target=x86_64-efi --efi-directory=$e --bootloader-id=gentoo --no-nvram || exit 1
            if [ -e "$e/EFI/BOOT/BOOTX64.EFI" ]; then
                grub-install --target=x86_64-efi --efi-directory=$e --removable --no-nvram || exit 1
            fi
            esp=$e; break
        done
        [ -n "$esp" ] || { say "$g merged, and no GRUB in a mounted ESP to reinstall"; exit 1; }
        grub-mkconfig -o /boot/grub/grub.cfg || exit 1
        step "$g: GRUB reinstalled in the ESP at $esp, grub.cfg remade"
    fi
    rm -rf /var/cache/binpkgs/* /var/cache/distfiles/*
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Linux.slackpkg
# Slackware 15.0 (testvm/bootstrap/slackware.sh: slackware15, slackware15-uefi;
# docs/design/slackware.md): slackpkg from the one mirror in
# /etc/slackpkg/mirrors, which brings the 15.0 tree's patches/.  Before the
# other Linux package managers: a full Slackware has rpm (series ap) as a tool.
# slackpkg's batch mode skips its own "run lilo?" questions, so a kernel
# upgrade is finished here as Slackware's UPGRADE.TXT says: the initrd again
# (generic kernels), then lilo, or the kernel and initrd copied to ELILO's
# ESP directories.  upgradepkg replaces a kernel in place (the running one's
# modules go with it), so this can't wait: vm-update reboots next.  .new
# config files stay as slackpkg leaves them in batch mode, listed.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ -e /etc/slackware-version ] && has slackpkg; then
    # t2-section: Linux.slackpkg
    kernels() { ls /var/lib/pkgtools/packages | grep '^kernel-\(huge\|generic\)-' | sort; }
    before=$(kernels)
    sp() { slackpkg -batch=on -default_answer=y "$@"; }
    sp update || exit 1
    # 20: nothing to upgrade.  50: slackpkg upgraded itself, and is run again.
    for i in 1 2; do
        sp upgrade-all; rc=$?
        [ $rc -eq 50 ] || break
    done
    [ $rc -eq 0 ] || [ $rc -eq 20 ] || { say "slackpkg upgrade-all failed ($rc)"; exit 1; }
    after=$(kernels)
    if [ "$before" != "$after" ]; then
        say "kernel packages now: $(echo $after)"
        k=$(ls -t /lib/modules | head -n 1)
        [ -d "/lib/modules/$k/kernel" ] || { say "no modules for the new kernel ($k)"; exit 1; }
        if [ -e /boot/initrd.gz ]; then
            # The command the initrd was made with, for the new kernel; else
            # the command generator's for this root.
            cmd=""
            [ -s /boot/initrd-tree/command_line ] && cmd=$(sed 's/ -k [^ ]*//' /boot/initrd-tree/command_line)
            [ -n "$cmd" ] || cmd=$(/usr/share/mkinitrd/mkinitrd_command_generator.sh -r -k "$k" | grep '^mkinitrd ')
            case $cmd in mkinitrd\ *) ;; *) say "no mkinitrd command for kernel $k"; exit 1 ;; esac
            sh -c "$cmd -k $k" || { say "mkinitrd failed for kernel $k"; exit 1; }
        fi
        if [ -e /boot/efi/EFI/Slackware/elilo.conf ]; then
            v=$(ls /boot/vmlinuz-generic-"$k" /boot/vmlinuz-huge-"$k" 2>/dev/null | head -n 1)
            [ -n "$v" ] || { say "no /boot/vmlinuz-*-$k for ELILO"; exit 1; }
            for d in /boot/efi/EFI/Slackware /boot/efi/EFI/BOOT; do
                [ -e "$d/elilo.conf" ] || continue
                cp "$v" "$d/vmlinuz" || exit 1
                if grep -q '^[[:space:]]*initrd=initrd.gz' "$d/elilo.conf"; then cp /boot/initrd.gz "$d/initrd.gz" || exit 1; fi
            done
            say "ELILO: $v copied to the ESP"
        elif [ -e /etc/lilo.conf ]; then
            lilo || { say "lilo failed"; exit 1; }
        else
            say "a new kernel, and neither ELILO's nor LILO's configuration"; exit 1
        fi
    fi
    new=$(find /etc -name '*.new' 2>/dev/null | sort)
    [ -z "$new" ] || say "config files left as .new: $(echo $new)"
    rm -rf /var/cache/packages/*
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Linux.abroot
# ABRoot (Vanilla OS: vanilla3; docs/design/vanillaos.md): two roots, the
# present one read-only, with no apt or dpkg on it (lpkg locks them away).
# `abroot upgrade` pulls the image abroot.json names (vanilla-os/gnome:latest)
# and, when it has moved, writes it into the other root, its kernel and
# initramfs into init/LABEL/, merges /etc into that root's upper and swaps the
# master grub.cfg: vm-update's clean reboot then boots the new root, and the
# one booted now is the previous.  With nothing newer it says "No update
# available", exits 1 and changes nothing: an update with nothing to merge.
# No --force: that rewrites the other root from the same image, a change no
# listing shows.  vanilla-updates.timer (vso's hourly upgrade) stays masked,
# as make-vanilla.sh left it: an upgrade of its own would race this one, and
# change a guest between updates.  abroot reads its prompts from stdin, which
# is this script (sh -s): /dev/null, or it eats the rest.
# t2-section: prologue
if [ "$(uname)" = Linux ] && { [ -f /usr/share/abroot/abroot.json ] || has abroot; }; then
    # t2-section: Linux.abroot
    has abroot || { say "ABRoot's abroot.json, and no abroot"; exit 1; }
    case $(systemctl is-enabled vanilla-updates.timer 2>/dev/null) in
    ""|masked|not-found) ;;
    *)
        step "vanilla-updates.timer masked"
        systemctl mask --now vanilla-updates.timer || exit 1
        ;;
    esac
    # The master grub.cfg's default, which only an upgrade's swap changes.
    dflt() { sed -n 's/^set default=//p' /boot/grub/grub.cfg 2>/dev/null | head -n 1; }
    was=$(dflt)
    out=$(abroot upgrade </dev/null 2>&1); rc=$?
    # Each progress bar's \r frames (~330,000 of them in an upgrade, one per
    # file synced) down to its last: GuestTest2's sweep reads 20,000 lines,
    # \r frames counted.  LC_ALL=C: a frame can cut a UTF-8 file name short.
    printf '%s\n' "$out" | LC_ALL=C sed 's/\x1b\[[0-9;]*m//g; s/\r*$//; s/.*\r//'
    if [ $rc -ne 0 ]; then
        if printf '%s\n' "$out" | grep -q 'No update available'; then
            say "ABRoot: no newer image, nothing changed"
            exit 0
        fi
        say "abroot upgrade failed ($rc)"
        exit 1
    fi
    now=$(dflt)
    [ "$now" = "$was" ] || step "ABRoot: upgraded the other root; grub.cfg's default is $now, was $was"
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Linux.snapcore
# Ubuntu Core (uc24, uc24-fde; docs/design/ubuntu-core.md): everything is a
# snap, the kernel (pc-kernel), the base that is / (core24), the gadget (pc)
# and snapd included, and `snap refresh` takes them all in one change.  A
# kernel, base, gadget or snapd refresh needs a reboot: snapd schedules one a
# few minutes out (a shutdown -r +N; under FDE it first reseals the TPM's
# keys for the new boot chain), and completes the change once booted, now
# and then after a second reboot.  So: refresh with --no-wait (it prints the
# change's ID) and wait here while the change runs.  Once it waits for that
# restart (its status Wait, or a shutdown scheduled), say "in-guest:
# snapcore: restart pending ID" and stop: vm-update reboots the guest
# itself, waits across whatever reboots more the change takes until snapd
# has no change in progress, and holds the auto-refreshes again
# (snapcore_settle there).  The auto-refreshes stay held all along
# (make-ubuntu-core.sh held them: one of snapd's own would race this one,
# or change a guest between updates); `snap refresh --hold` with no names
# holds them all for good, and a refresh asked for still runs.  Root's:
# snapd 2.78 lets only root refresh and list changes.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ -f /var/lib/snapd/modeenv ] && grep -qs '^ID=ubuntu-core$' /etc/os-release && has snap; then
    # t2-section: Linux.snapcore
    busy() { snap changes 2>/dev/null | awk 'NR > 1 && $2 ~ /^(Do|Doing|Undo|Undoing|Wait|Hold)$/ { printf "%s:%s ", $1, $2 }'; }
    restart_due() { [ -e /run/systemd/shutdown/scheduled ]; }
    pending() {  # CHANGE: stop here, for vm-update's reboot
        snap refresh --hold >/dev/null 2>&1 || true
        say "snapcore: restart pending $1"
        exit 0
    }
    # A change already under way (an earlier boot's): wait for it first.
    t0=$(date +%s)
    while b=$(busy) && [ -n "$b" ]; do
        case $b in *:Wait*) pending "${b%%:*}" ;; esac
        if restart_due; then pending "${b%%:*}"; fi
        [ $(($(date +%s) - t0)) -lt 1800 ] || { say "snapd still has changes in progress after 30 min: $b"; exit 1; }
        sleep 10
    done
    # The store or its CDN failing in transit (Go's url.Error, METHOD "URL":
    # why; the store's own refusals aren't): run again, up to 10 times 30 s
    # apart, as Linux.apt's snap refresh does.  OUT, the last try's output;
    # each earlier try's is printed.
    stored() {
        n=0
        while :; do
            out=$("$@" 2>&1); r=$?
            [ $r -ne 0 ] && [ $n -lt 10 ] && printf '%s\n' "$out" | grep -qE '(Get|Post|Head) "https://[^"]*": ' || break
            printf '%s\n' "$out"
            n=$((n + 1)); say "the snap store didn't answer: $* again in 30 s ($n of 10)"; sleep 30
        done
        return $r
    }
    stored snap refresh --list; rc=$?
    printf '%s\n' "$out"
    if printf '%s\n' "$out" | grep -q 'All snaps up to date'; then
        say "snapd: all snaps up to date, nothing changed"
        exit 0
    fi
    [ $rc -eq 0 ] || { say "snap refresh --list failed ($rc)"; exit 1; }
    stored snap refresh --no-wait || { printf '%s\n' "$out"; say "snap refresh --no-wait failed"; exit 1; }
    id=$out
    case $id in
    '' | *[!0-9]*) printf '%s\n' "$id"; say "snap refresh --no-wait printed no change ID"; exit 1 ;;
    esac
    say "snapcore: refresh change $id"
    t0=$(date +%s)
    while :; do
        st=$(snap changes 2>/dev/null | awk -v id="$id" '$1 == id { print $2 }')
        case $st in
        Done) break ;;
        Error | Undone | Abort) snap tasks "$id" 2>&1 | tail -n 20; say "the refresh (change $id) ended $st"; exit 1 ;;
        Wait) pending "$id" ;;
        esac
        if restart_due; then pending "$id"; fi
        [ $(($(date +%s) - t0)) -lt 7200 ] || { snap tasks "$id" 2>&1 | tail -n 20; say "the refresh (change $id) is still ${st:-unlisted} after 2 h"; exit 1; }
        sleep 10
    done
    snap refresh --hold >/dev/null || { say "snap refresh --hold failed"; exit 1; }
    say "updated"
    exit 0
# t2-section: prologue
fi

# t2-section: Linux.common
# --- Unfreeze -----------------------------------------------------------------------
# Each pin is recognized by its own mark, so an unfrozen guest is left alone.
# t2-section: prologue
if [ "$(uname)" = Linux ]; then
    # t2-section: Linux.common
    . /etc/os-release
    f=/etc/apt/sources.list.d/ubuntu.sources
    if [ -f $f ] && grep -q 'snapshot\.ubuntu\.com' $f; then
        # t2-section: Linux.apt
        # The archive serves the -security suites too.
        step "unfreezing: $f from snapshot.ubuntu.com to archive.ubuntu.com"
        sed -i -E 's#https://snapshot\.ubuntu\.com/ubuntu/[^/ ]+/?#http://archive.ubuntu.com/ubuntu/#' $f
    # t2-section: Linux.common
    fi
    if [ "$ID" = debian ] && has apt-get &&
        ! grep -hqsE '^[[:space:]]*(deb|URIs:)[[:space:]]' /etc/apt/sources.list /etc/apt/sources.list.d/*; then
        # t2-section: Linux.apt
        # Installed from the DVD alone, which the installer then disabled.
        f=/etc/apt/sources.list.d/debian.sources
        step "unfreezing: $f, Debian's mirror and security archive for $VERSION_CODENAME"
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
    # t2-section: Linux.common
    fi
    if [ "$ID" = devuan ] && [ -n "$VERSION_CODENAME" ] && has apt-get &&
        ! grep -hqsE "^[[:space:]]*(deb|Suites:)[[:space:]].*$VERSION_CODENAME-security" \
            /etc/apt/sources.list /etc/apt/sources.list.d/*; then
        # t2-section: Linux.apt
        # make-debian.sh's Devuan installs have the release's own suite
        # only: no security fixes, no point-release updates.
        f=/etc/apt/sources.list.d/devuan-updates.list
        step "unfreezing: $f, Devuan's $VERSION_CODENAME-security and -updates"
        printf 'deb http://deb.devuan.org/merged %s main\n' \
            $VERSION_CODENAME-security $VERSION_CODENAME-updates >$f || exit 1
    # t2-section: Linux.common
    fi
    f=/etc/pacman.d/mirrorlist
    if [ -f $f ] && grep -q 'archive\.archlinux\.org/repos/' $f; then
        # t2-section: Linux.pacman
        step "unfreezing: $f from Arch's archive to its mirrors"
        echo 'Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch' >$f
    # t2-section: Linux.common
    fi
    if grep -qs '^ID=artix' /etc/os-release && [ -f $f ] && ! grep -q '^Server = .*artix' $f; then
        # t2-section: Linux.pacman
        # Artix's builder once let pacstrap copy the Arch live system's
        # mirror list in (no -M), whose mirrors serve no Artix repository.
        # Artix's own, the file as its artix-mirrorlist package has it,
        # fetched through the mirror the guest was built from.
        step "unfreezing: $f, Artix's mirrors instead of Arch's"
        echo 'Server = https://mirrors.dotsrc.org/artix-linux/repos/$repo/os/$arch' >$f
        pkg=/var/cache/pacman/pkg/$(pacman -Sy >/dev/null && pacman -Sp artix-mirrorlist | sed 's|.*/||')
        pacman -Sw --noconfirm artix-mirrorlist && bsdtar -xOf "$pkg" etc/pacman.d/mirrorlist >$f.new &&
            mv $f.new $f || exit 1
        grep -q '^Server = .*artix' $f || { say "artix-mirrorlist gave no Artix mirrors"; exit 1; }
    # t2-section: Linux.common
    fi
    f=/etc/yum.repos.d/fedora-updates.repo
    if [ -f $f ] && sed -n '/^\[updates\]/,/^\[/p' $f | grep -q '^enabled=0'; then
        # t2-section: Linux.dnf
        step "unfreezing: $f, the updates repository on again"
        sed -i '/^\[updates\]/,/^\[/ s/^enabled=0/enabled=1/' $f
    # t2-section: Linux.common
    fi
    if grep -qs '^baseurl=https://dl\.rockylinux\.org/vault/rocky/' /etc/yum.repos.d/rocky*.repo; then
        # t2-section: Linux.dnf
        step "unfreezing: Rocky's repositories from the vault to the mirrors"
        sed -i -e 's/^#mirrorlist=/mirrorlist=/' \
            -e 's|^baseurl=https://dl\.rockylinux\.org/vault/rocky/[^/]*/|#baseurl=http://dl.rockylinux.org/$contentdir/$releasever/|' \
            /etc/yum.repos.d/rocky*.repo
    # t2-section: Linux.common
    fi
    if grep -qs '^baseurl=https\?://download\.opensuse\.org/history/' /etc/zypp/repos.d/*.repo; then
        # t2-section: Linux.zypper
        step "unfreezing: Tumbleweed's repositories from openSUSE's history to the live ones"
        sed -i -E 's#^baseurl=https?://download\.opensuse\.org/history/[^/]+/#baseurl=http://download.opensuse.org/#' \
            /etc/zypp/repos.d/*.repo
    # t2-section: Linux.common
    fi
    # t2-section: Linux.moss
    # AerynOS's builder pins its repository at one day's tag (version
    # "tag/2026-11-02-unstable-stream"); the live one is the unstable stream.
    # t2-section: Linux.common
    for f in $(grep -lsE '^[[:space:]]*version "tag/' /etc/moss/repo.d/*.kdl /etc/moss/system-model.kdl); do
        # t2-section: Linux.moss
        step "unfreezing: $f from its tag to the unstable stream"
        sed -i -E 's#^([[:space:]]*version )"tag/[^"]*"#\1"stream/unstable"#' "$f"
    # t2-section: Linux.common
    done
    # t2-section: Linux.zypper
    # The DVD it was installed from, which YaST adds as a repository after
    # the profile's own scripts have run: no DVD here, and zypper fails on
    # a repository it can't reach.
    # t2-section: Linux.common
    for f in $(grep -lsE '^baseurl=(cd|dvd):' /etc/zypp/repos.d/*.repo); do
        # t2-section: Linux.zypper
        step "dropping the install DVD's repository ($(basename "$f"))"
        rm -f "$f"
    # t2-section: Linux.common
    done
# t2-section: prologue
fi

# --- Room ---------------------------------------------------------------------------
# NILFS2 (zorin18's root) keeps what is deleted or overwritten, in its
# checkpoints, until its cleaner reclaims it, an hour later by default: an
# update would fill the disk with the files it replaced.  So reclaim runs the
# cleaner at once (no protection period) and waits for it.  But only while
# nothing else writes: the cleaner running beside a big apt run broke the
# file system (a bad DAT b-tree node, then read-only).  In between, it's
# suspended.  update-initramfs's "/sbin/fsck.nilfs2 doesn't exist" is
# harmless: nilfs-tools has no fsck (NILFS2 recovers from its log at mount),
# and fstab's pass for / is 0.
nilfs=
reclaim() { :; }
if [ "$(uname)" = Linux ] && [ "$(findmnt -no FSTYPE / 2>/dev/null)" = nilfs2 ] && has nilfs-clean; then
    # t2-section: Linux.common
    nilfs=$(findmnt -no SOURCE /)
    # journald off /var/log/journal for the rest of this boot, onto /run
    # (vm-update reads the run's journal before the reboot, /run's too).
    # After a cleaner run, what journald writes to its open system.journal
    # never reaches the disk, though files written and synced do: a clean
    # reboot then finds that file ONLINE ("corrupted or uncleanly shut
    # down") with the rest of the boot's entries gone.  Closed first, the
    # file stays clean (zorin18, GuestTest2 t2v-20261118T0027-d7c8 and
    # throwaway boots on Chopin, 2026-11-18).
    ! has journalctl || journalctl --relinquish-var >/dev/null 2>&1 || :
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
# t2-section: prologue
fi
# t2-section: Linux.common
# Omarchy's installer keeps a read-only snapshot of the fresh install,
# @factory (for its factory reset), beside @ at the btrfs top level, outside
# the mounted tree.  It holds on to every file an update replaces: on
# omarchy's 14 GiB btrfs, update 1 ran out of room in the middle of a kernel.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ "$(findmnt -no FSTYPE / 2>/dev/null)" = btrfs ] && has btrfs; then
    # t2-section: Linux.common
    top=$(mktemp -d)
    if mount -o subvolid=5 "$(findmnt -nvo SOURCE /)" "$top"; then
        if [ -d "$top/@factory" ]; then
            step "deleting the installer's read-only @factory snapshot"
            btrfs subvolume delete "$top/@factory" || { umount "$top"; exit 1; }
        fi
        umount "$top"
    fi
    rmdir "$top"
# t2-section: prologue
fi
# t2-section: Linux.common
# Ubuntu's guided ZFS install (Subiquity: bpool holds /boot) leaves no
# /etc/zfs/zfs-list.cache, so systemd has no mount unit for /boot, and
# mounts the ESP's /boot/efi first: zfs-mount.service then mounts bpool's
# /boot over it (ubuntu26-zfs, 2026-11-07: /boot/efi showed bpool's empty
# directory), and a grub or shim update would miss the real ESP.  The cache
# zed's zfs-list-cacher keeps (the OpenZFS root-on-ZFS guides' setup) gives
# zfs-mount-generator what it needs to make one, and boot-efi.mount is
# ordered after it.  It's written here as the cacher would; zed keeps it
# current.  And for this boot, /boot/efi is mounted again, over /boot.
# t2-section: prologue
if [ "$(uname)" = Linux ] && [ -d /run/systemd/system ] && [ "$(findmnt -no FSTYPE /boot 2>/dev/null)" = zfs ] &&
    [ ! -d /etc/zfs/zfs-list.cache ] && [ -f /etc/zfs/zed.d/history_event-zfs-list-cacher.sh ]; then
    # t2-section: Linux.common
    step "ZFS: listing the pools' file systems in /etc/zfs/zfs-list.cache, for /boot's mount unit"
    props=$(sed -n '/^PROPS="/,/"$/p' /etc/zfs/zed.d/history_event-zfs-list-cacher.sh | tr -d '\\\n' |
        sed 's/^PROPS="//; s/"$//')
    case $props in name,mountpoint,*) ;; *) say "can't read zfs-list-cacher's PROPS"; exit 1 ;; esac
    mkdir -p /etc/zfs/zfs-list.cache || exit 1
    for p in $(zpool list -H -o name); do
        zfs list -H -t filesystem -o "$props" -r "$p" >"/etc/zfs/zfs-list.cache/$p" || exit 1
    done
    if findmnt -n /boot/efi >/dev/null && [ ! -d /boot/efi/EFI ]; then
        b=$(findmnt -no SOURCE /boot)
        umount /boot && umount /boot/efi && zfs mount "$b" && mount /boot/efi && [ -d /boot/efi/EFI ] ||
            { say "could not mount /boot/efi again, over /boot"; exit 1; }
        say "/boot/efi mounted again, over /boot"
    fi
# t2-section: prologue
fi

case $(uname) in
Linux)
    # t2-section: Linux.apt
    # With dpkg: BigLinux's apt-get (and apt) only point the user at pamac.
    # t2-section: Linux.common
    if has apt-get && has dpkg; then
        # t2-section: Linux.apt
        export DEBIAN_FRONTEND=noninteractive
        # The guest's own apt jobs (apt-daily, unattended-upgrades, PackageKit)
        # often start at boot, and would take apt's locks between our steps:
        # stop them for this boot (runtime state only, in /run).  Then wait
        # for any still holding a lock: DPkg::Lock::Timeout covers dpkg's
        # lock, not the lists'.
        # Pop!_OS's two updaters run apt-get update too: pop-system-updater
        # at boot, and pop-upgrade's daemon when root's ssh login starts its
        # user units (pop-upgrade-notify's "pop-upgrade release check" a
        # minute on, and the updater's session half asks over D-Bus).  The
        # second took the lists' lock from apt-get clean (pop24, GuestTest2
        # t2v-20261118T1426-76be attempt 1): masked, D-Bus can't start them
        # again.  The updater's KillMode=process leaves a running apt-get
        # to finish; the update below waits it out.
        if [ -d /run/systemd/system ]; then
            systemctl mask --runtime --now packagekit.service >/dev/null 2>&1
            if has pop-system-updater || has pop-upgrade; then
                systemctl mask --runtime --now com.system76.SystemUpdater.service pop-upgrade.service >/dev/null 2>&1
            fi
            systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service \
                >/dev/null 2>&1
        fi
        # Pop!_OS: systemd-boot's menu has kernelstub's two entries, the
        # newest kernel (Pop_OS-current) and the one before (Pop_OS-oldkern),
        # but make-from-live.sh once left loader.conf without a default, and
        # systemd-boot then takes the last by name: oldkern.  Its kernel is
        # the one an update's autoremove purges the modules of, so the reboot
        # after one stopped in emergency mode (no FAT charset for /boot/efi).
        l=/boot/efi/loader/loader.conf
        if [ -f /etc/kernelstub/configuration ] && [ -f $l ] &&
            ! grep -qE '^[[:space:]]*default[[:space:]]' $l; then
            step "boot menu: Pop_OS-current by default"
            echo "default Pop_OS-current" >>$l || exit 1
        fi
        # Kexec handover (KHO), on by default in Ubuntu 26.04's 7.0 kernel
        # (CONFIG_KEXEC_HANDOVER_ENABLE_DEFAULT=y), reserves a scratch area
        # at boot that only movable pages may use.  On a 2 GiB guest, when
        # the reservation succeeds (on some boots, not others), that is
        # 1.28 GB, and the kernel keeps ~450 MB: ubuntu26-zfs's ARC, which
        # isn't movable, starved it during an update, kswapd0 failing page
        # allocations with all its free memory free_cma (GuestTest2
        # t2v-20261118T0027-d7c8).  Nothing here kexecs, so kho=off, added
        # the way kdump-tools.cfg adds crashkernel= (GRUB's drop-ins; this
        # update-grub writes it, the next boot boots with it).  Under ZFS the
        # update can't run on this boot: ubuntu26-zfs hung (SSH gone, no
        # console output) at the end of an update on a boot whose scratch
        # reservation had succeeded ("Memory: 190160K/2008888K available",
        # GuestTest2 t2v-20261118T0617-4a11, attempt 1 on choubox).  So
        # exit 4 with "reboot first": vm-update reboots the guest and runs
        # this script again, which then finds bko-kho.cfg and goes on.
        if grep -qs '^CONFIG_KEXEC_HANDOVER_ENABLE_DEFAULT=y' "/boot/config-$(uname -r)" &&
            ! grep -qw 'kho=off' /proc/cmdline && [ -f /etc/default/grub ] && has update-grub &&
            [ ! -f /etc/default/grub.d/bko-kho.cfg ]; then
            step "kernel command line: kho=off (kexec handover's scratch area took most of RAM)"
            mkdir -p /etc/default/grub.d &&
                echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT kho=off"' >/etc/default/grub.d/bko-kho.cfg &&
                update-grub || exit 1
            if [ -d /sys/module/zfs ]; then
                say "reboot first: kho=off, before ZFS's ARC runs the update short of kernel memory"
                exit 4
            fi
        fi
        # snapd (Ubuntu): the image's refresh hold has lapsed and it has never
        # refreshed, so the upgrade's new snapd (its postinst restarts it)
        # starts an auto-refresh at once, and the reboot stopped that in
        # mid-change: its "Start snap services" task waits on a systemctl
        # that a shutdown never answers, and systemd killed snapd after 90 s
        # (ubuntu26-uefi, t2v-20261118T0027-d7c8; reproduced on its overlay,
        # hwctl's refresh).  So auto-refreshes are held for good, as
        # make-ubuntu-core.sh holds them (they would change a guest between
        # updates too), and the snaps refresh after apt instead, by hand, the
        # hold notwithstanding.  Only where snapd answers.
        snapd=
        if has snap && [ -f /var/lib/snapd/state.json ] && snap changes >/dev/null 2>&1; then
            snapd=yes
            snap_settle() {
                t0=$(date +%s)
                while b=$(snap changes 2>/dev/null | awk 'NR > 1 && $2 ~ /^(Do|Doing|Undo|Undoing|Wait)$/ { printf "%s:%s ", $1, $2 }'); [ -n "$b" ]; do
                    [ $(($(date +%s) - t0)) -lt 1800 ] || { say "snapd still has changes in progress after 30 min: $b"; exit 1; }
                    sleep 5
                done
            }
            snap_settle
            if ! snap refresh --time 2>/dev/null | grep -q '^hold: *forever'; then
                step "snapd: auto-refreshes held (the snaps refresh with each update instead)"
                snap refresh --hold >/dev/null || { say "snap refresh --hold failed"; exit 1; }
            fi
        fi
        # Error-Mode=any: a repository that failed to download is an error
        # (to retry), not a warning that leaves the old lists in place.
        apt="apt-get -y -o DPkg::Lock::Timeout=900 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
        # PackageKit's apt hook asks it over D-Bus to reload after every
        # change, and with it masked above it fails loudly each time ("Unit
        # packagekit.service is masked"): apt runs here with a copy of the
        # guest's apt.conf.d less that hook (in /run, gone at the reboot).
        # Only APT_CONFIG's file is read before the parts directory, so
        # that's where it is named (-o comes too late).
        # AnduinOS's snapshot manager takes a Btrfs snapshot before every dpkg
        # run (its Pre-Invoke hook, above a 40 GiB free-space floor): the
        # image keeps the installer's baselines only (docs/design/anduinos.md,
        # Decision 5), so its hook goes from that copy too, its
        # NeverAutoRemove line kept.
        sm=90-anduinos-btrfs-snapshots-manager
        if [ -f /etc/apt/apt.conf.d/20packagekit ] || [ -f /etc/apt/apt.conf.d/$sm ]; then
            rm -rf /run/bko-apt.conf.d && cp -a /etc/apt/apt.conf.d /run/bko-apt.conf.d &&
                rm -f /run/bko-apt.conf.d/20packagekit &&
                echo 'Dir::Etc::parts "/run/bko-apt.conf.d";' >/run/bko-apt.conf || exit 1
            if [ -f /run/bko-apt.conf.d/$sm ]; then
                grep -v 'Invoke' /etc/apt/apt.conf.d/$sm >/run/bko-apt.conf.d/$sm || exit 1
                say "apt runs without the snapshot manager's pre- and post-dpkg hooks"
            fi
            export APT_CONFIG=/run/bko-apt.conf
        fi
        tries=0
        until $apt -o APT::Update::Error-Mode=any update; do
            tries=$((tries + 1)); [ $tries -lt 60 ] || exit 1; sleep 15
        done
        # fetched CMD...: an apt-get that downloads, run again while it fails
        # at a download, up to 10 times 30 s apart (Launchpad's PPAs answered
        # every few requests with a 503 for over an hour on 2026-11-17, and
        # apt's own three tries came too close together).  apt stops before
        # dpkg runs when it can't fetch everything, and what it did fetch
        # stays in its cache for the next try.  A last failure's output ends
        # the log, for GuestTest2's flake rules to read.  The same for the
        # lists' or the archives' lock held by an apt of the guest's own:
        # DPkg::Lock::Timeout waits for dpkg's lock only, and apt-get clean
        # takes both of the others.
        fetched() {
            n=0
            while :; do
                { "$@" 2>&1; echo $? >/run/bko-fetch.rc; } | tee /run/bko-fetch.log
                r=$(cat /run/bko-fetch.rc)
                [ "$r" -ne 0 ] && [ $n -lt 10 ] || break
                if grep -q '^E: Failed to fetch ' /run/bko-fetch.log; then
                    n=$((n + 1)); say "a download failed: apt-get again in 30 s ($n of 10)"
                elif grep -qE '^E: Could not get lock /var/(lib/apt/lists|cache/apt/archives)/lock' /run/bko-fetch.log; then
                    n=$((n + 1)); say "another apt holds apt's lock: apt-get again in 30 s ($n of 10)"
                else
                    break
                fi
                sleep 30
            done
            rm -f /run/bko-fetch.rc /run/bko-fetch.log
            return "$r"
        }
        # deps_first PKG...: the packages again, each after those of them it
        # depends on (its candidate's Depends and Pre-Depends, as apt-cache
        # lists them; a cycle is cut where it closes).  The batches below go
        # in this order, so no package is set up before the newer library it
        # was built against: on pikaos4 and pikaos4-luks (2026-11-18)
        # libglib2.0-0t64 2.90.0 came in a batch before libpcre2-8-0 10.48
        # (its Depends asks for >= 10.22 only), and every glib program run
        # until libpcre2's batch warned that the old libpcre2-8.so.0 had "no
        # version information available".  A package apt-cache doesn't list
        # keeps its place.
        deps_first() {
            [ $# -gt 0 ] || return 0
            apt-cache depends --important "$@" 2>/dev/null | awk -v want="$*" '
                function visit(p,   i, n, d) {
                    if (p in done) return
                    done[p] = 1
                    n = split(deps[p], d, " ")
                    for (i = 1; i <= n; i++) if (d[i] in up) visit(d[i])
                    print p
                }
                BEGIN { nw = split(want, w, " "); for (i = 1; i <= nw; i++) up[w[i]] = 1 }
                /^[^ ]/ { cur = $1; sub(/:.*/, "", cur); next }
                $1 ~ /^\|?(Pre)?Depends:$/ && $2 !~ /^</ { d = $2; sub(/:.*/, "", d); deps[cur] = deps[cur] " " d }
                END { for (i = 1; i <= nw; i++) visit(w[i]) }'
        }
        # In batches, the downloads deleted after each: a desktop guest's
        # 16 GB disk hasn't room for all of a big update's packages at once
        # beside their unpacked files.  dist-upgrade does the rest (packages
        # that replace others).
        # snapd's postinst runs setcap on snap-confine unconditionally, which
        # fails on NILFS2: no xattrs, so no file capabilities.  Its
        # snap-confine stays without them, as make-from-live.sh built it, and
        # no snaps are installed.  So dpkg finds a setcap first that lets a
        # failure on snap-confine pass, and snapd takes its security updates
        # (older images held it instead).  Only snap-confine's: other
        # packages fall back on their own when setcap fails
        # (libgstreamer1.0-0's gst-ptp-helper and mtr-tiny's mtr-packet to
        # setuid), and a setcap that let any NILFS2 file pass made their
        # postinsts skip it (zorin18, GuestTest2 t2v-20261118T2305-edbb:
        # "Setcap worked! gst-ptp-helper is not suid!").  apt runs dpkg with
        # PATH set to DPkg::Path (default /usr/sbin:/usr/bin:/sbin:/bin), so
        # that's where it goes; dpkg passes PATH on to the maintainer scripts.
        bkobin=
        apt0=$apt
        if [ -n "$nilfs" ] && dpkg -s snapd >/dev/null 2>&1; then
            if apt-mark showhold | grep -qx snapd; then
                apt-mark unhold snapd >/dev/null || exit 1
                step "snapd unheld: setcap's failure on NILFS2 passes instead"
            fi
            real=
            for f in /usr/sbin/setcap /sbin/setcap; do [ -x $f ] && { real=$f; break; }; done
            if [ -n "$real" ]; then
                dp= && eval "$(apt-config shell dp DPkg::Path)"
                bkobin=$(mktemp -d) || exit 1
                trap '[ -z "$bkobin" ] || rm -rf "$bkobin"' EXIT
                trap 'exit 1' HUP INT TERM
                # setcap [-q] [-v] [-n ROOTID] then CAPS FILE pairs (CAPS may
                # be - for stdin, or -r): a failure passes only when every
                # FILE is snapd's snap-confine, on NILFS2; any other keeps
                # setcap's own exit status.
                { echo '#!/bin/sh'; echo "real=$real"; cat <<'EOF'
"$real" "$@" && exit 0
s=$? files=
sc=$(readlink -f /usr/lib/snapd/snap-confine)
while [ $# -gt 0 ]; do
    case $1 in
    -q | -v) shift ;;
    -n) [ $# -ge 2 ] || exit $s; shift 2 ;;
    *)
        [ $# -ge 2 ] && [ -e "$2" ] && [ "$(readlink -f "$2")" = "$sc" ] &&
            [ "$(findmnt -no FSTYPE -T "$2" 2>/dev/null)" = nilfs2 ] || exit $s
        files="$files $2"; shift 2 ;;
    esac
done
[ -n "$files" ] || exit $s
echo "bko: setcap skipped on NILFS2 (no xattrs):$files" >&2
EOF
                } >"$bkobin/setcap" && chmod 755 "$bkobin/setcap" || exit 1
                apt="$apt -o DPkg::Path=$bkobin:${dp:-$PATH}"
            fi
        fi
        # KDE neon pins its own ubuntu-release-upgrader and base-files to its
        # archive (Pin-Priority 1100, /etc/apt/preferences.d/*neon*), so where
        # the image holds Ubuntu's newer build apt's candidate is neon's, an
        # upgrade that is a downgrade, as neon means it.  apt=$apt0 below
        # drops the option again.
        if grep -qs '^ID=neon$' /etc/os-release; then apt="$apt --allow-downgrades"; fi
        # TRIM while dpkg unpacks: vm-update's overlay (vm-run --ram --discard,
        # in the host's RAM disk) keeps every block the guest writes until a
        # TRIM hands it back, and trim.sh's comes only after the reboot.  On a
        # root without online discard each download and each file a package
        # replaced stayed there: elementary8's 445 upgrades, which grew / by a
        # few MiB, peaked at 6.5 GiB (2026-11-17).  So fstrim every 30 s
        # until the upgrade is done (or this script has gone): beside writes
        # it is safe, as the weekly fstrim.timer is.  Not on NILFS2, whose
        # cleaner reclaim runs between batches instead.  A LUKS volume opened
        # without discards (elementary8-luks's, it seems) passes none on, and
        # fstrim fails there, quietly: its overlay still holds every block.
        trimmer=
        if [ -z "$nilfs" ] && has fstrim; then
            ( while sleep 30 && kill -0 $$ 2>/dev/null; do fstrim -a >/dev/null 2>&1; done ) </dev/null >/dev/null 2>&1 &
            trimmer=$!
        fi
        # elementary OS ships its apps as system flatpaks, each behind a stub
        # deb (org.gnome.epiphany, io.elementary.camera, ...) whose prerm
        # removes the flatpak and whose postinst installs it again, "|| true".
        # Upgrading the stubs moved every app to a new runtime
        # (io.elementary.Platform 8.2 -> 9.1, 1.2 GB) while the old one stayed
        # (flatpak remove keeps runtimes), all in the same apt run as the HWE
        # kernel and the split linux-firmware (865 MB of archives): elementary8's
        # 11.4 GiB root, 3.2 GiB free, filled, and Epiphany's postinst failed
        # ("Not enough disk space"), left uninstalled (GuestTest2
        # t2v-20261118T0027-d7c8).  So the flatpaks are updated first and the
        # runtimes nothing uses any more removed (1.1 GB back, measured on an
        # overlay of elementary8 on 2026-11-18): the stubs' postinsts then find
        # their app's runtime there.  GL.default//24.08 stays: the epiphany
        # stub installs it by name, which pins it.
        if grep -qs '^ID=elementary$' /etc/os-release && has flatpak; then
            fl0=$(flatpak list --system --columns=ref,active 2>/dev/null)
            n=0
            until flatpak update --system --noninteractive -y; do
                n=$((n + 1)); [ $n -lt 10 ] || { say "flatpak update failed"; exit 1; }
                say "flatpak update failed: again in 30 s ($n of 10)"; sleep 30
            done
            flatpak uninstall --system --unused --noninteractive -y || { say "flatpak uninstall --unused failed"; exit 1; }
            [ "$(flatpak list --system --columns=ref,active 2>/dev/null)" = "$fl0" ] ||
                step "flatpaks updated, unused runtimes removed"
        fi
        apt-mark showhold >/run/bko-held || exit 1
        set -- $(apt list --upgradable 2>/dev/null | sed -n 's|/.*||p' | grep -vxF -f /run/bko-held)
        order=$(deps_first "$@"); [ -z "$order" ] || set -- $order
        while [ $# -gt 0 ]; do
            batch=
            while [ $# -gt 0 ] && [ $(echo $batch | wc -w) -lt 40 ]; do batch="$batch $1"; shift; done
            fetched $apt install --only-upgrade $batch && fetched $apt clean || exit 1
            reclaim
        done
        fetched $apt dist-upgrade && $apt autoremove --purge && fetched $apt clean || exit 1
        if [ -n "$snapd" ]; then
            snap_settle
            # Run again while the store or its CDN fails in transit, up to 10
            # times 30 s apart: a POST to api.snapcraft.io that timed out, a
            # download's connection reset (ubuntu26-zfs and ubuntu26-raid,
            # GuestTest2 t2v-20261118T1426-76be attempt 1).  snap prints
            # those as Go's url.Error, METHOD "URL": why; the store's own
            # refusals aren't.  A snap whose lane did refresh keeps it, so
            # every try's output counts for the step line.
            all= n=0
            until out=$(snap refresh 2>&1); do
                printf '%s\n' "$out"; all="$all$out
"
                if [ $n -lt 10 ] && printf '%s\n' "$out" | grep -qE '(Get|Post|Head) "https://[^"]*": '; then
                    n=$((n + 1)); say "the snap store didn't answer: snap refresh again in 30 s ($n of 10)"
                    sleep 30; snap_settle
                else
                    say "snap refresh failed"; exit 1
                fi
            done
            printf '%s\n' "$out"; out="$all$out"
            # A snap refreshed is an update no package listing shows.  Sorted:
            # snap refresh prints them in no fixed order.
            refreshed=$(printf '%s\n' "$out" | sed -n 's/^\([^ ]*\) .* refreshed$/\1/p' | LC_ALL=C sort -u | tr '\n' ' ')
            [ -z "$refreshed" ] || step "snapd: refreshed $refreshed"
            snap_settle
        fi
        [ -z "$trimmer" ] || { kill $trimmer; wait $trimmer; } 2>/dev/null
        apt=$apt0
        if [ -n "$bkobin" ]; then
            rm -rf "$bkobin" || exit 1
            bkobin=; trap - EXIT HUP INT TERM
        fi
        # A helper whose package falls back to setuid when setcap fails (a
        # postinst that runs setcap and chmod u+s on the same path:
        # gst-ptp-helper, mtr-packet), on NILFS2, which holds no file
        # capabilities: given that fallback here when it has neither.
        # make-from-live.sh's unsquashfs -no-xattrs dropped the live image's
        # capabilities, and the setcap above let every NILFS2 file's failure
        # pass until 2026-11-19, so zorin18's had neither.  A path the
        # postinst names by a variable isn't found; none on zorin18 does.
        if [ -n "$nilfs" ]; then
            for f in $(grep -l setcap /var/lib/dpkg/info/*.postinst 2>/dev/null |
                xargs -r sed -n "s#.*chmod u+s \\(/[^ ;|&\"')]*\\).*#\\1#p" | sort -u); do
                [ -f "$f" ] && [ ! -u "$f" ] && [ -z "$(getcap "$f" 2>/dev/null)" ] || continue
                step "$f setuid, its package's fallback: NILFS2 holds no file capabilities"
                chmod u+s "$f" || exit 1
            done
        fi
        # Linux Mint (make-from-live.sh): the live session's casper outlived
        # the live packages' removal, and runs at every shutdown.  Purged if
        # nothing else goes with it.
        if [ -f /etc/linuxmint/info ] && dpkg -s casper >/dev/null 2>&1 &&
            [ "$($apt -s purge casper 2>/dev/null | grep -c '^Purg ')" = 1 ]; then
            step "purging casper"
            $apt purge casper || exit 1
        fi
        # Pop!_OS and Zorin OS (make-from-live.sh): fdisk and ethtool, which
        # the live system's removal list takes and a real install keeps.
        # Installed only when missing (pkgset.sh's listing shows the change).
        if grep -qsE '^ID=(pop|zorin)$' /etc/os-release; then
            miss=
            for p in fdisk ethtool; do
                [ "$(dpkg-query -W -f '${db:Status-Status}' $p 2>/dev/null)" = installed ] || miss="$miss $p"
            done
            [ -z "$miss" ] || { fetched $apt install $miss && fetched $apt clean || exit 1; }
        fi
        # xfsprogs 7.2.0 (Debian sid's; pikaos4-luks takes it) enables
        # xfs_healer@-.service, the healer for /, on every system: on a root
        # that isn't XFS its support check exits 255, the unit fails at every
        # boot and the guest comes up "degraded" (upstream bug, not fixed in
        # 7.2.1 or master).  Masked where / isn't XFS (in /etc, so it stays
        # masked); an XFS root (pikaos4) keeps it.  Nothing to do without
        # systemd or the unit, or once masked.
        if [ -d /run/systemd/system ] && has systemctl &&
            [ "$(systemctl show -p LoadState --value 'xfs_healer@-.service' 2>/dev/null)" = loaded ]; then
            rootfs=$(findmnt -no FSTYPE / 2>/dev/null)
            if [ -n "$rootfs" ] && [ "$rootfs" != xfs ]; then
                step "xfs_healer@-.service masked: / is $rootfs, not XFS"
                systemctl mask 'xfs_healer@-.service' >/dev/null || exit 1
                systemctl reset-failed 'xfs_healer@-.service' >/dev/null 2>&1 || :
            fi
        fi
    # t2-section: Linux.common
    elif [ -e /run/ostree-booted ] && has rpm-ostree; then
        # t2-section: Linux.ostree
        # An image-mode system (Fedora's atomic desktops, Bazzite; kinoite44):
        # /usr is the image's and read-only, so dnf can't, though it's there.
        # rpm-ostree pulls the image the origin names (kinoite44's follows
        # the :44 tag) and stages it as a new deployment; vm-update's clean
        # reboot boots it (ostree-finalize-staged writes it at shutdown), and
        # ostree keeps the one booted now as the rollback and drops the one
        # before.  No `cleanup -r`: the guest keeps two deployments.
        # cleanup -b -m drops the pull's temporary files and rpm-ostree's
        # metadata cache, which would stay in the image; vm-update's
        # qcow2-punch.py hands back what the dropped deployment's objects
        # held.  A new deployment is a step: an image can change with no
        # package's version.
        # mcelog decodes Intel machine checks only: on the hosts' AMD CPUs it
        # fails at every boot, and the guest comes up "degraded".  The image
        # has it, so mask it (in /etc, which the staged deployment takes on);
        # ks-ostree.cfg now does at the install.  Nothing to do where the
        # image has no mcelog.
        case $(systemctl is-enabled mcelog.service 2>/dev/null) in
        ""|not-found|masked) ;;
        *)
            step "mcelog masked"
            systemctl mask mcelog.service || exit 1
            ;;
        esac
        booted() {
            rpm-ostree status --json | python3 -c 'import json, sys
d = [x for x in json.load(sys.stdin)["deployments"] if x.get(sys.argv[1])]
print(d[0]["checksum"] if d else "")' "$1"
        }
        # fetched CMD...: an rpm-ostree that downloads, run again while it
        # fails on the network.  quay.io's CDN drops a layer's download now
        # and then ("proxy failure: ... unexpected EOF", "Reading large
        # regfile: unexpected end of file": kinoite44 on cinqcent, twice in
        # t2v-20261118T2305-edbb, at layer 10 and then 41 of 42) and the
        # image proxy's one reconnect fails too.  The layers already imported
        # stay in the repo, so the next try fetches only those after them.
        # rpm-ostreed's own transaction (an automatic update) is waited out
        # the same way.  Any other error ends it.
        fetched() {
            n=0
            while :; do
                { "$@" 2>&1; echo $? >/run/bko-fetch.rc; } | tee /run/bko-fetch.log
                r=$(cat /run/bko-fetch.rc)
                [ "$r" -ne 0 ] && [ $n -lt 10 ] &&
                    grep -qiE '^error: .*(EOF|end of file|connection reset|connection refused|broken pipe|timed out|timeout|TLS handshake|name resolution|no such host|network is unreachable|50[234] |Transaction in progress)' /run/bko-fetch.log ||
                    break
                n=$((n + 1)); say "a download failed: rpm-ostree again in 30 s ($n of 10)"; sleep 30
            done
            rm -f /run/bko-fetch.rc /run/bko-fetch.log
            return "$r"
        }
        was=$(booted booted) || exit 1
        fetched rpm-ostree upgrade || exit 1
        staged=$(booted staged) || exit 1
        if [ -n "$staged" ] && [ "$staged" != "$was" ]; then
            step "ostree: deployment $staged staged, in place of $was"
        fi
        rpm-ostree cleanup -b -m || exit 1
    # t2-section: Linux.dnf
    # Not BigLinux's dnf (and yum), a script that only execs pamac.
    # t2-section: Linux.common
    elif has dnf && ! grep -qs '^exec pamac' "$(command -v dnf)"; then
        # t2-section: Linux.dnf
        # Fedora's server install has mcelog, which decodes Intel machine
        # checks only: on the hosts' AMD CPUs it fails at every boot, and the
        # guest comes up "degraded".  make-fedora.sh's kickstart now leaves
        # it out.
        if [ -e /etc/fedora-release ] && rpm -q mcelog >/dev/null 2>&1; then
            dnf -y remove mcelog || exit 1
        fi
        # tpm2-tools: only dracut's weak dependency, and not on the Server
        # DVD the kickstart installs from offline, so a fresh guest lacks it
        # until an online dnf pulls it in.  Installed by name, so a Fedora
        # guest has it like rocky10 does.
        if [ -e /etc/fedora-release ] && ! rpm -q tpm2-tools >/dev/null 2>&1; then
            dnf -y install --refresh tpm2-tools || exit 1
        fi
        dnf -y upgrade --refresh && dnf clean all || exit 1
    # t2-section: Linux.common
    elif has pacman; then
        # t2-section: Linux.pacman
        # BigLinux: big-first-boot runs once, at the first boot (Timeshift's
        # first snapshot, pacman -R of the VM tools it doesn't need, then of
        # itself), and auto-unlock-pacman at every boot after it, which holds
        # db.lck while it populates the keyrings and then deletes db.lck,
        # whoever holds it.  So pacman waits until neither runs or waits to.
        for u in big-first-boot.service auto-unlock-pacman.service; do
            [ "$(systemctl show -p LoadState --value $u 2>/dev/null)" = loaded ] || continue
            waited=0
            while s=$(systemctl is-active $u 2>/dev/null); [ "$s" = activating ] || [ "$s" = deactivating ] ||
                systemctl list-jobs --no-legend 2>/dev/null | awk -v u=$u '$2 == u { f = 1 } END { exit !f }'; do
                [ $waited -lt 600 ] || { say "$u is still $s after 600 s: pacman would fight it for db.lck"; exit 1; }
                [ $waited -gt 0 ] || say "waiting for $u ($s) to finish before pacman runs"
                sleep 5; waited=$((waited + 5))
            done
            [ $waited -eq 0 ] || say "$u done after ${waited} s ($s)"
        done
        # dinit on a JFS root (artix-dinit): dinit-rc's root-ro remounts /
        # read-only, fsck-root checks it, and early-root-rw.target remounts it
        # read-write while net-lo, hostname and udevd already run.  JFS's
        # remount to rw (jfs_mount_rw) frees its block map and makes it again,
        # and a lookup that reads an inode from disk meanwhile reads the map
        # unlocked: ip's, for net-lo, oopsed on the NULL sbi->bmap in diRead
        # (Linux 7.2.14), net-lo failed and with it the whole boot, sshd
        # never started (GuestTest2 t2v-20261118T1159-2749, cinqcent; one
        # boot in several).  So / stays read-write, as the initramfs mounted
        # it after its fsck hook checked it: root-ro only makes it rshared,
        # and fsck-root is a target, with the same place in the order.  In
        # /etc/dinit.d, ahead of /usr/lib/dinit.d.  bootstrap/artix.sh writes
        # the same two files.
        if [ -f /usr/lib/dinit.d/root-ro ] && [ -f /usr/lib/dinit.d/fsck-root ] &&
            [ "$(findmnt -no FSTYPE / 2>/dev/null)" = jfs ] && ! grep -qs bko-jfs-root /etc/dinit.d/root-ro; then
            step "dinit: / stays read-write at boot (JFS's ro to rw remount races lookups)"
            cat >/etc/dinit.d/root-ro <<'EOF' &&
# bko-jfs-root: / stays read-write, as the initramfs mounted it (its fsck
# hook checked it): JFS's remount from ro to rw races other lookups.
type    = scripted
command = /bin/mount --make-rshared /
restart = false
EOF
            cat >/etc/dinit.d/fsck-root <<'EOF' || exit 1
# bko-jfs-root: the initramfs checked / before mounting it read-write.
type       = internal
depends-on = early-prepare.target
depends-ms = udevd-early
after      = udev-settle
EOF
        fi
        # The keyrings first: packages signed by keys newer than the guest's
        # keyring would fail to verify.  Omarchy's pacman hook stops a plain
        # pacman upgrade (for "omarchy update", which is interactive) unless
        # told this is meant.
        export OMARCHY_ALLOW_DIRECT_PACMAN=1
        # EndeavourOS's live image has plasma-x11-session, which Plasma 6.8
        # conflicts with; --noconfirm answers no to removing it, and stops.
        if grep -qs '^ID="*endeavouros' /etc/os-release && pacman -Qq plasma-x11-session >/dev/null 2>&1; then
            pacman -Rdd --noconfirm plasma-x11-session || exit 1
        fi
        keyrings=$(pacman -Qq archlinux-keyring artix-keyring biglinux-keyring cachyos-keyring chaotic-keyring endeavouros-keyring manjaro-keyring omarchy-keyring 2>/dev/null)
        # fetched CMD...: a pacman that downloads, run again while it fails
        # at a download, up to 10 times 30 s apart (a mirror that stalled
        # on one file: "Operation too slow", endeavouros-grub 2026-11-17).
        # pacman upgrades nothing unless it has every package, and keeps
        # what it fetched in its cache for the next try.  A last failure's
        # output ends the log, for GuestTest2's flake rules to read.
        fetched() {
            n=0
            while :; do
                { "$@" 2>&1; echo $? >/run/bko-fetch.rc; } | tee /run/bko-fetch.log
                r=$(cat /run/bko-fetch.rc)
                [ "$r" -ne 0 ] && [ $n -lt 10 ] &&
                    grep -qE 'failed to retrieve some files|failed retrieving file|failed to synchronize all databases' /run/bko-fetch.log ||
                    break
                n=$((n + 1)); say "a download failed: pacman again in 30 s ($n of 10)"; sleep 30
            done
            rm -f /run/bko-fetch.rc /run/bko-fetch.log
            return "$r"
        }
        # Manjaro: the installer ranks the mirrors once and nothing ranks them
        # again, so a guest keeps the mirrors of the day it was built; some
        # stop syncing, and pacman reads their old databases (manjaro, built
        # 2026-08-25, its first mirrors frozen in June: "there is nothing to
        # do" on 2026-11-17, and archlinux-keyring downgraded).  pacman-mirrors
        # --status exits 0 while the first mirror is up to date on the
        # guest's branch, 4 when it is behind, 5 when the list names a
        # mirror Manjaro no longer has, 1 when the list has no Server line or
        # repo.manjaro.org's status can't be read (tried again like
        # fetched).  Then --fasttrack 5 ranks five mirrors up to date on
        # that branch (pacman-mirrors.conf's, kept).  Offline it writes the
        # list anyway, from its own old pool, and exits 0, so the old list
        # comes back unless --status then says 0, and the update fails:
        # pacman on mirrors known to be behind would update nothing.  A
        # status that can't be read at all leaves the list alone, and pacman
        # tries it.
        if has pacman-mirrors && grep -qs '^ID="*manjaro' /etc/os-release; then
            f=/etc/pacman.d/mirrorlist why= n=0
            while s=$(pacman-mirrors --status 2>&1); r=$?
                [ $r -eq 1 ] && [ $n -lt 10 ] && printf '%s\n' "$s" | grep -q 'Downloading status failed'; do
                n=$((n + 1)); say "Manjaro's mirror status didn't download: again in 30 s ($n of 10)"; sleep 30
            done
            case $r in
            0) ;;
            1)  if printf '%s\n' "$s" | grep -q 'Downloading status failed'; then
                    say "Manjaro's mirror status didn't download: $f stays as it is"
                else
                    why="it had no mirror pacman-mirrors could read"
                fi ;;
            4) why="its first mirror was behind" ;;
            5) why="it named a mirror Manjaro no longer has" ;;
            *) printf '%s\n' "$s"; say "pacman-mirrors --status exited $r"; exit 1 ;;
            esac
            if [ -n "$why" ]; then
                cp -p $f /run/bko-mirrorlist || exit 1
                n=0
                until o=$(pacman-mirrors --fasttrack 5 2>&1) && s=$(pacman-mirrors --status 2>&1); do
                    if [ $n -ge 10 ]; then
                        cp -p /run/bko-mirrorlist $f; rm -f /run/bko-mirrorlist
                        printf '%s\n' "$o" "$s"
                        say "pacman-mirrors --fasttrack 5 gave no list whose first mirror is up to date: $f is as it was"
                        exit 1
                    fi
                    n=$((n + 1)); say "pacman-mirrors --fasttrack 5 gave no up-to-date list: again in 30 s ($n of 10)"; sleep 30
                done
                rm -f /run/bko-mirrorlist
                step "mirrors: $f ranked again by pacman-mirrors --fasttrack 5 ($why)"
            fi
        fi
        # GRUB, when the upgrade takes a new one.  Arch's grub only says ":: To
        # use the new features provided in this GRUB update, it is recommended
        # to install it to the MBR or UEFI ... # grub-install ... # grub-mkconfig"
        # (endeavouros-grub, GuestTest2 t2v-20261118T0617-4a11, 2026-11-18):
        # left undone, the old boot code (the core image in the ESP, or after
        # the MBR) loads /boot/grub's new modules and the new grub-mkconfig's
        # grub.cfg, and a symbol or version mismatch there stops the boot.
        # Manjaro's and BigLinux's install-grub hook and Garuda's garuda-hooks
        # do it themselves; again here it only writes the same files.  How it
        # was installed is read before the upgrade (a hook may rewrite the
        # ESP's image during it), as the builders install it (arch/install.sh,
        # arch/install-live.sh, bootstrap/artix.sh):
        # - UEFI (the guest booted so): the one EFI/ID/grubx64.efi in a vfat
        #   ESP mounted at /efi, /boot/efi or /boot, ID being the
        #   --bootloader-id (endeavouros, manjaro, Garuda, BigLinux), and the
        #   fallback EFI/BOOT/BOOTX64.EFI when it is a copy of that image
        #   (Calamares's installEFIFallback; the guests have no NVRAM entry,
        #   --no-nvram, so their firmware boots the fallback);
        # - BIOS: i386-pc, to the one disk under /boot/grub's file system
        #   whose first sector is GRUB's boot.img (arch, cachyos, artix-*).
        # Anything else (no GRUB boot code, two candidates, /boot on no one
        # disk) is named and left alone rather than guessed, and only when
        # the upgrade takes a new grub.
        grubv() { pacman -Qo /usr/bin/grub-install 2>/dev/null | sed 's/.* is owned by //'; }
        grub_was=$(grubv) grub_target= grub_dev= grub_id= grub_fb= grub_fbnote= grub_skip=
        if [ -z "$grub_was" ]; then
            :
        elif [ -d /sys/firmware/efi ]; then
            gn=0
            for ge in /efi /boot/efi /boot; do
                ls "$ge"/ >/dev/null 2>&1     # an automounted ESP mounts here
                awk -v m="$ge" '$2 == m && $3 == "vfat" { f = 1 } END { exit !f }' /proc/mounts || continue
                for gf in "$ge"/EFI/*/grubx64.efi; do
                    [ -f "$gf" ] || continue
                    gd=${gf%/grubx64.efi}; gd=${gd##*/}
                    case $gd in [Bb][Oo][Oo][Tt]) continue ;; esac
                    gn=$((gn + 1)) grub_dev=$ge grub_id=$gd
                done
            done
            if [ $gn -eq 1 ]; then
                grub_target=x86_64-efi
                for gf in "$grub_dev"/EFI/*/*; do
                    case ${gf#"$grub_dev"/EFI/} in [Bb][Oo][Oo][Tt]/[Bb][Oo][Oo][Tt][Xx]64.[Ee][Ff][Ii]) ;; *) continue ;; esac
                    if [ "$(sha256sum <"$gf")" = "$(sha256sum <"$grub_dev/EFI/$grub_id/grubx64.efi")" ]; then
                        grub_fb=$gf
                    else
                        grub_fbnote="; ${gf#"$grub_dev"/} left as it was: not a copy of EFI/$grub_id/grubx64.efi"
                    fi
                done
            elif [ $gn -eq 0 ]; then
                grub_skip="no EFI/*/grubx64.efi in a vfat ESP mounted at /efi, /boot/efi or /boot"
            else
                grub_skip="$gn EFI/*/grubx64.efi in the mounted ESPs, the last EFI/$grub_id in $grub_dev"
            fi
        elif [ -d /boot/grub/i386-pc ]; then
            gs=$(findmnt -no SOURCE -T /boot/grub 2>/dev/null | sed 's/\[.*//')
            gd=$(lsblk -lnps -o NAME,TYPE "$gs" 2>/dev/null | awk '$2 == "disk" { print $1 }' | sort -u)
            if [ -z "$gd" ]; then
                grub_skip="/boot/grub's file system ($gs) is on no disk lsblk shows"
            elif [ "$(echo "$gd" | wc -l)" -ne 1 ]; then
                grub_skip="/boot/grub's file system ($gs) is on more than one disk: $(echo $gd)"
            elif ! head -c 512 "$gd" | grep -aq GRUB; then
                grub_skip="the first sector of $gd, under /boot/grub's file system, isn't GRUB's"
            else
                grub_target=i386-pc grub_dev=$gd
            fi
        else
            grub_skip="not booted by UEFI, and no /boot/grub/i386-pc"
        fi
        # Then only the keyrings the repositories have newer: pacman -S of an
        # older one downgrades it, --needed or not (archlinux-keyring on
        # manjaro's stale mirrors).  -Su never downgrades.
        fetched pacman -Sy --noconfirm || exit 1
        up=
        [ -z "$keyrings" ] || up=$(pacman -Qqu $keyrings)
        # TRIM while pacman unpacks: vm-update's overlay (vm-run --ram
        # --discard, in the host's RAM disk) keeps every block the guest
        # writes until a TRIM hands it back, and trim.sh's comes only after
        # the reboot.  endeavouros's ext4 has no online discard: its upgrade
        # of 2026-11-17 (2.2 GiB downloaded, 6.7 GiB unpacked, / grown by
        # 0.4) passed 6 GiB of overlay with the files it replaced still in
        # it, and outgrew three reservations.  So fstrim every 30 s until
        # the upgrade is done (or this script has gone): beside writes it is
        # safe, as the weekly fstrim.timer is.  The downloads stay in the
        # cache until pacman -Scc; a LUKS volume opened without discards
        # passes none on, and fstrim fails there, quietly.
        trimmer=
        if has fstrim; then
            ( while sleep 30 && kill -0 $$ 2>/dev/null; do fstrim -a >/dev/null 2>&1; done ) </dev/null >/dev/null 2>&1 &
            trimmer=$!
        fi
        { [ -z "$up" ] || fetched pacman -S --noconfirm --needed $up; } && fetched pacman -Su --noconfirm || exit 1
        [ -z "$trimmer" ] || { kill $trimmer; wait $trimmer; } 2>/dev/null
        # A new grub: install it again where the old one was (read above),
        # and its grub.cfg.
        grub_now=$(grubv)
        if [ -n "$grub_was" ] && [ -n "$grub_now" ] && [ "$grub_now" != "$grub_was" ]; then
            case $grub_target in
            x86_64-efi)
                grub-install --target=x86_64-efi --efi-directory="$grub_dev" --bootloader-id="$grub_id" --no-nvram || exit 1
                if [ -n "$grub_fb" ]; then
                    cp "$grub_dev/EFI/$grub_id/grubx64.efi" "$grub_fb" || exit 1
                    grub_fbnote=", and its copy ${grub_fb#"$grub_dev"/}"
                fi
                grub-mkconfig -o /boot/grub/grub.cfg || exit 1
                step "$grub_was -> $grub_now: GRUB reinstalled (x86_64-efi) as EFI/$grub_id in the ESP at $grub_dev$grub_fbnote; grub.cfg remade" ;;
            i386-pc)
                grub-install --target=i386-pc "$grub_dev" || exit 1
                grub-mkconfig -o /boot/grub/grub.cfg || exit 1
                step "$grub_was -> $grub_now: GRUB reinstalled (i386-pc) to $grub_dev; grub.cfg remade" ;;
            *)  say "warning: $grub_was -> $grub_now, and GRUB is not installed again: $grub_skip" ;;
            esac
        fi
        yes | pacman -Scc >/dev/null
    # t2-section: Linux.tukit
    # A transactional system (MicroOS: microos; docs/design/aeon.md): / is a
    # read-only snapper snapshot (fstab's ro), the btrfs default, and only
    # transactional-update changes it; zypper, there too, can't.  Its dup
    # runs zypper in a new snapshot of the default and makes that the
    # default (--drop-if-no-change: none is kept when nothing changed);
    # vm-update's clean reboot boots it, and then checks that the snapshot
    # booted is the one named on the "tukit: default" line: health-checker
    # sets the default back to the last good one, and reboots into that,
    # when a new snapshot fails its first boot.
    # The snapshots stay bounded: `transactional-update cleanup` gives the
    # ones before the booted snapshot snapper's number algorithm only while
    # the booted one is the default, which is now (the image is always
    # merged after the last update's reboot), and snapper's number cleanup
    # then deletes what is over its NUMBER_LIMIT.  So it runs first, as the
    # last update's after-reboot step, and a deletion is a step.
    # t2-section: Linux.common
    elif has transactional-update &&
        awk '$1 !~ /^#/ && $2 == "/" && $4 ~ /(^|,)ro(=[^,]*)?(,|$)/ { f = 1 } END { exit !f }' /etc/fstab; then
        # t2-section: Linux.tukit
        dflt() { btrfs subvolume get-default / | sed 's/.* path //'; }
        # snapper's list, not /.snapshots: a dropped snapshot leaves its empty directory.
        snaps() { snapper --no-dbus --csvout list --columns number | sed '1d; /^0$/d' | tr '\n' ' '; }
        booted=$(findmnt -no FSROOT / | sed 's|^/||') was=$(dflt)
        [ -n "$was" ] && [ "$booted" = "$was" ] ||
            { say "tukit: booted $booted, but the default is $was: an update is pending, or was rolled back"; exit 1; }
        before=$(snaps)
        transactional-update --non-interactive cleanup && snapper --no-dbus cleanup number || exit 1
        after=$(snaps)
        [ "$after" = "$before" ] || step "snapper: snapshots ${before% } cleaned up to ${after% }"
        # zypper's exit codes stay inside: 0, 102 and 103 are its 0, else 1;
        # "zypper: nothing to update" is a 0 that drops the snapshot.  (103,
        # zypper updated itself, leaves the rest to the next update.)
        transactional-update --non-interactive --drop-if-no-change dup || { say "transactional-update dup failed ($?)"; exit 1; }
        now=$(dflt)
        [ "$now" = "$was" ] || step "transactional-update: $now is the default, in place of $was"
        say "tukit: default $now"
        # The cache is in /var, which is writable.
        zypper -n clean -a
    # t2-section: Linux.common
    elif has zypper; then
        # t2-section: Linux.zypper
        # 103: zypper updated itself and wants to run again.  100-102: done,
        # with something to say (a reboot is needed, for one).
        # Leap is a release, updated within it by `zypper up` (a dup would
        # also switch vendors and downgrade); Tumbleweed is rolling: dup.
        how=$( . /etc/os-release 2>/dev/null; [ "${ID:-}" = opensuse-leap ] && echo up || echo dup)
        for i in 1 2 3; do
            # --no-recommends: as the install (autoinst.xml), which leaves
            # them out; otherwise a dup brings some 1 GB of firmware.
            zypper -n --gpg-auto-import-keys $how --auto-agree-with-licenses --no-recommends; rc=$?
            [ $rc -eq 103 ] || break
        done
        [ $rc -eq 0 ] || [ $rc -ge 100 -a $rc -le 102 ] || exit 1
        zypper -n clean -a
    # t2-section: Linux.common
    elif has xbps-install; then
        # t2-section: Linux.xbps
        # xbps itself first, as xbps-install asks.
        xbps-install -Syu xbps || [ $? -eq 17 ] || exit 1   # 17: it is current already
        xbps-install -yu || exit 1
        # Kernels are preserve=yes: an upgrade keeps the old one, its
        # initramfs and modules, until vkpurge removes them (not the one
        # running, nor any a package still owns).
        if has vkpurge; then vkpurge rm all || exit 1; fi
        xbps-remove -yO || exit 1
    # t2-section: Linux.common
    elif has apk; then
        # t2-section: Linux.apk
        apk upgrade -U || exit 1
        # Alpine: findmnt (util-linux's), which busybox lacks and HoltTest's
        # health check runs.  Installed only when missing (pkgset.sh's
        # listing shows the change).
        if grep -qs '^ID=alpine$' /etc/os-release && ! apk info -e findmnt >/dev/null 2>&1; then
            apk add findmnt || exit 1
        fi
    # t2-section: Linux.common
    elif has eopkg; then
        # t2-section: Linux.eopkg
        # Solus: a curated rolling release, upgraded whole (it takes no
        # partial upgrade).  eopkg's triggers run usysconf, whose
        # clr-boot-manager step mounts the ESP at /boot itself (it isn't in
        # fstab), copies the new kernel and initrd there and makes its
        # systemd-boot entry, and drops kernels but the newest and the
        # running one.  Then the package cache, which eopkg keeps.
        eopkg up -y || exit 1
        eopkg dc || exit 1
    # t2-section: Linux.common
    elif has moss; then
        # t2-section: Linux.moss
        # AerynOS: each sync makes a new state and keeps the ones before it
        # (each a /usr of hard links into /.moss's assets, with its boot
        # entry on the ESP).  This one and the one before stay, as on NixOS;
        # then the assets no state holds any more, and the downloads, which
        # cache prune keeps while the repository has them (moss fetches
        # again what it needs).
        moss -y sync -u && moss -y state remove --keep 2 && moss -y cache prune || exit 1
        rm -rf /.moss/cache/downloads
        # The sync swapped /usr under the running systemd, and none of moss's
        # triggers (/usr/share/moss/triggers) re-executes it.  PID 1 starts
        # every process through the systemd-executor it opened at boot (the
        # old state's, /.moss/root/N/usr/lib/systemd/systemd-executor), which
        # loads libsystemd-shared-OLD.so from /usr/lib/systemd, and a sync
        # that moves systemd (261 to 262, GuestTest2 t2v-20261118T0027-d7c8)
        # leaves only the new one there: each exec fails with status 127,
        # so the reboot's ExecStops, swapoffs and umounts all fail
        # (blk-availability, dracut-shutdown, systemd-random-seed, the swap
        # partition's 15 units, /efi, /tmp...).  The reexec puts PID 1 on
        # the new state's systemd and executor: the reboot is then clean (a
        # sync of 261.4 to 262.2 in an overlay, 2026-11-18).
        if [ -d /run/systemd/system ]; then
            systemctl daemon-reexec || exit 1
        fi
    # t2-section: Linux.common
    else
        say "no package manager found"; exit 3
    fi
    # t2-section: prologue
    ;;
FreeBSD)
    # t2-section: FreeBSD
    export ASSUME_ALWAYS_YES=yes
    if has hbsd-update; then
        # HardenedBSD (uname says FreeBSD): its base system's binary updates
        # come from hbsd-update, which exits 0 when the guest is current;
        # it has neither freebsd-update nor pkgbase.  Packages only where
        # pkg is installed (pkg -N), as the guest's install has none: any
        # other pkg command would install pkg first (ASSUME_ALWAYS_YES), and
        # then fail, as 15-STABLE's repository can't be read (2026-11: its
        # meta.conf names data.tzst, it holds data.pkg).
        hbsd-update || exit 1
        if pkg -N >/dev/null 2>&1; then pkg upgrade -y || exit 1; fi
    elif pkg info -e FreeBSD-runtime || pkg info -e GhostBSD-runtime; then
        # pkgbase: the base system is packages too, upgraded with the rest.
        # GhostBSD (uname says FreeBSD) has it as GhostBSD-* (repository
        # GhostBSD-base) and no freebsd-update; its Update Station runs pkg
        # too, into a new boot environment where / is ZFS.
        pkg update -f && pkg upgrade -y || exit 1
    else
        PAGER=cat freebsd-update --not-running-from-cron fetch install; rc=$?
        [ $rc -eq 0 ] || [ $rc -eq 2 ] || exit 1      # 2: nothing to install
        pkg upgrade -y || exit 1
    fi
    ! pkg -N >/dev/null 2>&1 || pkg clean -ay
    # t2-section: prologue
    ;;
DragonFly)
    # t2-section: DragonFly
    export ASSUME_ALWAYS_YES=yes
    pkg upgrade -y || exit 1
    # The base system: DragonFly publishes it in binary only as each point
    # release's install image, and its fixes land on the release branch in
    # between.  So it is rebuilt from that branch's head, as DragonFly's own
    # upgrade steps do it (buildworld, buildkernel, installkernel,
    # installworld, upgrade), unless that is what it runs: /var/db/bko-base
    # holds the commit the last rebuild came from (before the first, the
    # release's tag stands for it).  A rebuild that fails, installworld half
    # done included, fails the update, whose overlay vm-update throws away.
    # From DragonFly's mirror on GitHub, over HTTPS: its own git server
    # (/usr/Makefile's) speaks only git://, which nothing authenticates.
    # git only for this, then gone again: just what installing it added,
    # since pkg autoremove would also take most of the guest's own
    # packages, which its install marks automatic.
    branch=DragonFly_RELEASE_$(uname -r | sed 's/-.*//; s/\./_/')
    repo=https://github.com/DragonFlyBSD/DragonFlyBSD.git
    gitpkgs=
    if ! has git; then
        pkg query %n | sort >/tmp/bko-pkgs && pkg install -y git-lite || exit 1
        gitpkgs=$(pkg query %n | sort | comm -13 /tmp/bko-pkgs -)
    fi
    head=$(git ls-remote $repo refs/heads/$branch | cut -f1)
    [ -n "$head" ] || { say "no $branch at $repo"; exit 1; }
    if [ -f /var/db/bko-base ]; then
        built=$(cat /var/db/bko-base)
    else
        tag=$(uname -v | sed -n 's/^DragonFly \(v[0-9.]*\)-RELEASE .*/\1/p')
        built=$(git ls-remote $repo "refs/tags/$tag^{}" | cut -f1)
    fi
    if [ "$head" = "$built" ]; then
        say "base system: current, $branch at $head"
    else
        say "base system: rebuilding from $branch at $head (built from ${built:-?})"
        # What a rebuild cut short left (src-create-shallow won't clone
        # over a /usr/src).
        rm -rf /usr/src /usr/obj/* || exit 1
        make -C /usr src-create-shallow BRANCH=$branch GITURL_SRC=$repo >/dev/null || exit 1
        commit=$(git -C /usr/src rev-parse HEAD) || exit 1
        # The running kernel's configuration: the release's is X86_64_GENERIC.
        k=$(uname -i)
        [ -f /usr/src/sys/config/$k ] || { say "no kernel configuration $k in /usr/src"; exit 1; }
        # The log goes with the objects: the sources, objects and log take
        # some 3 GB, which trim.sh's zeroing hands back after the reboot.
        # The kernel and modules stripped, as kernel.old is: with their debug
        # symbols the release's take twice the room, and /boot has 1 GiB,
        # kernel.old in it too.  Each step's start goes to the output, for
        # its time: buildworld prints none.
        n=$(sysctl -n hw.ncpu) log=/usr/obj/bko-build.log
        mk() {
            say "base system: make $* ($(date +%T))"
            (cd /usr/src && make "$@") >>$log 2>&1 || { tail -n 30 $log; say "base system: make $* failed"; exit 1; }
        }
        mk -j$n buildworld
        mk -j$n buildkernel KERNCONF=$k
        mk installkernel KERNCONF=$k INSTALLSTRIPPED=1
        mk installworld
        mk upgrade
        echo "$commit" >/var/db/bko-base
        # The sshd still running hands each new connection to the binaries
        # just replaced, which needn't match it: the new one takes over.
        /etc/rc.d/sshd restart >/dev/null || exit 1
        rm -rf /usr/src /usr/obj/*
        step "base system: rebuilt from $branch at $commit ($(date +%T)), running from the next boot"
    fi
    [ -z "$gitpkgs" ] || pkg delete -y $gitpkgs || exit 1
    pkg clean -ay || exit 1
    # t2-section: prologue
    ;;
OpenBSD)
    # t2-section: OpenBSD
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
    pkg_add -u -I || exit 1
    # t2-section: prologue
    ;;
NetBSD)
    # t2-section: NetBSD
    if has apk; then
        # AlpineBSD.  Its kernel (netbsd-kernel-generic-bin: NetBSD's own
        # build of the release) and its base (netbsd-base, built from the
        # release's sources) are apk packages, upgraded with the rest.
        # apk checks TLS against ca-certificates-bundle, which apk-tools
        # depends on: a guest without its file gets it first with the check
        # off, the package's signature still checked against the repository
        # key.  ca-certificates: the same roots, one file each and hashed in
        # /etc/ssl/certs, for other TLS clients.  openssh-sftp-server: the
        # sftp-server dropbear runs for scp and sftp, which a guest built
        # before netbsd/install.sh added it lacks.  apk update, not add -U:
        # add goes on with the old index when a repository can't be fetched.
        if [ ! -s /etc/ssl/certs/ca-certificates.crt ]; then
            step "fetching the CA certificates unchecked"
            apk add -U --check-certificate=no ca-certificates-bundle &&
                apk fix --check-certificate=no ca-certificates-bundle || exit 1
        fi
        apk update && apk add ca-certificates openssh-sftp-server && apk upgrade || exit 1
        # The kernel package's version needn't be its kernel's: 11.1-r0
        # (2026-11) holds 11.0's, the Jul 30 build, beside an 11.1 base.
        # Nothing here can mend that; say so, rather than leave it to be
        # found in uname after the reboot.
        k=$(strings /netbsd | sed -n 's/^NetBSD \([0-9][0-9.]*\) (.*/\1/p' | head -n 1)
        p=$(apk info -v 2>/dev/null | sed -n 's/^netbsd-kernel-generic-bin-\([0-9.]*\)-r[0-9]*$/\1/p')
        [ "$k" = "$p" ] ||
            say "AlpineBSD's netbsd-kernel-generic-bin $p holds NetBSD ${k:-?}'s kernel; the reboot runs that"
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
        # certificates' hashes) and the rc.d scripts, which belong to the
        # etc set (a daily's new /etc/rc.d/network fails postinstall's rc
        # check otherwise, t2v-20261117T1639-dc6b; local settings live in
        # rc.conf, never in rc.d).
        arch=$(uname -m) rel=$(uname -r | sed 's/_.*//')
        # The running kernel's configuration, "(GENERIC)" in uname -v:
        # KERNEL=AUTO would need config(1), from the comp set, not here.
        kernel=$(uname -v | sed -n 's/.*(\([A-Za-z0-9_]*\)).*/\1/p')
        if [ -z "$(ls /etc/openssl/certs)" ]; then
            certctl rehash || exit 1
            step "certctl rehash: the base set's CA certificates"
        fi
        if ! grep -q '^ip6addrctl_policy=ipv4_prefer' /etc/rc.conf; then
            printf 'ip6addrctl=YES\nip6addrctl_policy=ipv4_prefer\n' >>/etc/rc.conf
            step "rc.conf: IPv4 first"
        fi
        /etc/rc.d/ip6addrctl start >/dev/null || exit 1
        pkgs=https://cdn.netbsd.org/pub/pkgsrc/packages/NetBSD/$arch/$rel/All
        if ! has pkgin; then
            step "installing pkgin and sysupgrade from $pkgs"
            PKG_PATH=$pkgs pkg_add pkgin || exit 1
        fi
        # pkgin follows the release the guest runs (11.0 to 11.1: from the
        # update after the one that brought the new base), once pkgsrc has
        # packages for it: the branch can say 11.2 before pkgsrc does.  Only
        # a guest whose repository isn't this release's asks: the summary
        # pkgin reads first, pkg_summary.bz2 (ftp follows the CDN's
        # redirects, amd64 to x86_64 and on to the quarterly branch).  A 404
        # is "not yet"; any other failure is tried again, and said with
        # ftp's own words: once, the probe of a guest already on 11.1's
        # repository failed, silently, as "no packages"
        # (t2v-20261118T0942-226e, choubox), every other run's got through.
        repo=$(cat /usr/pkg/etc/pkgin/repositories.conf 2>/dev/null)
        if [ "$repo" != "$pkgs" ]; then
            got= n=0
            while [ $n -lt 3 ]; do
                n=$((n + 1))
                if err=$(ftp -V -o /dev/null "$pkgs/pkg_summary.bz2" 2>&1 >/dev/null); then got=yes; break; fi
                case $err in *"404 Not Found"*) got=no; break ;; esac
                sleep 10
            done
            case $got in
            yes)
                echo "$pkgs" >/usr/pkg/etc/pkgin/repositories.conf || exit 1
                step "pkgin's repository is $pkgs" ;;
            no) say "no packages at $pkgs yet: pkgin stays on $repo" ;;
            *) say "$pkgs/pkg_summary.bz2 didn't download ($(echo $err)): pkgin stays on $repo" ;;
            esac
        fi
        has sysupgrade || pkgin -y install sysupgrade || exit 1
        releasedir=https://nycdn.netbsd.org/pub/NetBSD-daily/netbsd-${rel%%.*}/latest/$arch
        cat >/usr/pkg/etc/sysupgrade.conf.new <<EOF
RELEASEDIR="$releasedir"
KERNEL=${kernel:-GENERIC}
SETS=AUTO
POSTINSTALL_AUTOFIX="obsolete mtree opensslcertsrehash rc"
ETCUPDATE=no
AUTOCLEAN=yes
EOF
        if cmp -s /usr/pkg/etc/sysupgrade.conf.new /usr/pkg/etc/sysupgrade.conf; then
            rm -f /usr/pkg/etc/sysupgrade.conf.new
        else
            mv /usr/pkg/etc/sysupgrade.conf.new /usr/pkg/etc/sysupgrade.conf || exit 1
            step "sysupgrade.conf: the netbsd-${rel%%.*} daily, kernel ${kernel:-GENERIC}"
        fi
        # The build's checksums, sets' and kernels': the same as at the last
        # sysupgrade, and there's nothing new to install (sysupgrade would
        # fetch and extract every set again, some 500 MiB of writes).
        sums=/var/db/bko-netbsd-daily.sha512
        ftp -V -o $sums.sets "$releasedir/binary/sets/SHA512" >/dev/null 2>&1 &&
            ftp -V -o $sums.kernel "$releasedir/binary/kernel/SHA512" >/dev/null 2>&1 &&
            cat $sums.sets $sums.kernel >$sums.new || { rm -f $sums.sets $sums.kernel $sums.new; exit 1; }
        rm -f $sums.sets $sums.kernel
        if cmp -s $sums.new $sums; then
            say "the netbsd-${rel%%.*} daily is the one installed: no sysupgrade"
            rm -f $sums.new
        else
            sysupgrade auto || exit 1
            mv $sums.new $sums || exit 1
            step "base system: sysupgrade to the netbsd-${rel%%.*} daily"
            # The sshd still running hands each new connection to the
            # binaries just replaced, which needn't match it: the new one
            # takes over.  (Its pid file comes a moment after start returns.)
            /etc/rc.d/sshd restart >/dev/null || exit 1
            n=0; until /etc/rc.d/sshd status >/dev/null 2>&1; do
                n=$((n + 1)); [ $n -lt 15 ] || { say "sshd didn't come back"; exit 1; }; sleep 1
            done
        fi
        pkgin -y update && pkgin -y upgrade && pkgin -y clean || exit 1
    fi
    # t2-section: prologue
    ;;
SunOS)
    # t2-section: SunOS
    # A new boot environment when the update needs one; the reboot after
    # this activates it.  4: nothing to update.
    # Boot environments: each update that makes one leaves the last behind,
    # so only the newest two are kept (the running one and the one before),
    # never one marked N (running) or R (next boot).  beadm list -H:
    # name;uuid;flags;mountpoint;space;policy;created (seconds).
    beadm list -H 2>/dev/null | awk -F';' '{ print $7, $3, $1 }' | sort -rn | awk 'NR > 2 && $2 !~ /[NR]/ { print $3 }' |
    while read -r be; do
        say "removing the old boot environment $be"
        beadm destroy -Ffs "$be" || exit 1
    done || exit 1
    # Downloads not kept once installed: OpenIndiana's image doesn't flush
    # them (a first update there is ~5 GiB of cache in the overlay).
    pkg set-property flush-content-cache-on-success True || exit 1
    pkg update -v; rc=$?
    [ $rc -eq 0 ] || [ $rc -eq 4 ] || exit 1
    # t2-section: prologue
    ;;
Haiku)
    # t2-section: Haiku
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
    ! unrefreshed || { say "a repository couldn't be refreshed"; exit 1; }
    # t2-section: prologue
    ;;
Redox)
    # t2-section: Redox
    # Every installed package: kernel, initfs and relibc too (the boot
    # loader's stages in the boot partitions only redox_installer writes).
    # --yes, as nothing may ask.  pkg exits 0 whatever happens (2026-11),
    # so its errors are read from what it says, with case: grep panics on
    # Redox today.  Under --yes, pkg doesn't retry a download that times
    # out ("Download error: ... timed out"), which is common: again, here.
    tries=0
    while :; do
        pkg --yes update >/tmp/bko-pkg.log 2>&1; rc=$?
        cat /tmp/bko-pkg.log
        case $(cat /tmp/bko-pkg.log) in
        *"Download error"*|*"timed out"*)
            tries=$((tries + 1))
            [ $tries -lt 5 ] || { say "pkg's downloads timed out $tries times"; exit 1; }
            say "a download timed out; again in 20 s"
            sleep 20; continue ;;
        *error:*|*Error:*) say "pkg failed"; exit 1 ;;
        esac
        [ $rc -eq 0 ] || { say "pkg exited $rc"; exit 1; }
        break
    done
    rm -rf /tmp/pkg_download /tmp/bko-pkg.log
    # t2-section: prologue
    ;;
*)
    say "no updater for $(uname)"; exit 3 ;;
esac
if [ -n "$nilfs" ]; then
    # Once more for what dist-upgrade and autoremove replaced, so that the
    # image leaves with that room free, then the cleaner as it was.
    reclaim
    nilfs-clean -r "$nilfs" || exit 1
fi
say "updated"
