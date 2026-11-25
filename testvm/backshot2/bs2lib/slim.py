"""The slim step (README "Slimming"): a source whose room is too big for the small hosts
has the bulk that layout-drift testing doesn't need removed in its preparation, before
the dump and the backup, so its cases' RAM disks, its backup and its tools are smaller.

Which sources.  slimmed(SOURCE) is true for a source whose full room (HoltTest1's D and
MEM, testvm/holttest1d/ram-sizes.tsv: room.full_of()) is over SMALL_CEILING, the small
hosts' cmp-run.sh --ceiling (copland, cage and czerny: 9.0 GiB of RAM disk, 10.1 GiB of
memory), i.e. one room.py's ceiling rule keeps off them, unless it is one of FULL_FAT:
ubuntu26-luks, kinoite44 and garuda stay as they are (the user's choice, 2026-11-25;
HoltTest1 and BackShot1 still cover full guests).  Alpine's four keep bs2's own room
and are never slimmed.

How.  script(SOURCE) is a POSIX sh script that ht1.sh runs as root in the booted
source, in the preparation's own overlay (HT_SOURCE_PREP: the shared base guests in
~/backotter-work/vms are never written), before the dump; its last line is REBOOT, so
ht1.sh boots the slimmed source once more from cold, and everything after (its boot
state, health, the ostree/ABRoot boots of the other root, the dump, the backup, the
tools copied from the overlay, every case drawn from the dump) is the slimmed
source's.  It prefers the distro's own tools (apt-get clean, pacman -Scc, eopkg
delete-cache, journalctl --vacuum-time, flatpak uninstall, snap remove), and deletes
paths only where no tool fits.  Each step prints "SLIM step NAME ROOT FREED_KIB HOW",
measured with du before and after over the paths it touches; parse() reads them back
into the preparation's slim.json.  "SLIM used before|after KIB" is the real filesystems'
df used, each device once, but the du of the mount on one in DU_FS (nilfs2), whose df
isn't its live data ("SLIM usedby du DEVICE FSTYPE MOUNT": slim.json's used_du, and a
note saying why).

Never touched (NEVER, and the script's guard refuses any path under them): the
partition layout, filesystem types, mount options, labels and UUIDs (nothing here
runs a partitioner, mkfs, tune2fs or mount -o on a guest filesystem but ABRoot's
remount of its read-only root for the step, put back after), the boot chain (/boot,
/efi, the ESP, /usr/share/grub, plymouth's theme, the kernels and their modules and
initramfs: old kernels stay, see OLD_KERNELS), /etc, /home, /root, /srv (where the A7
content recipes plant), /var/srv and /var/home (ABRoot's), /var/lib/abroot/etc (each
root's /etc), and bko-holt's own logs.  One exception, made by a distro tool and not
by a path: `snap remove` deletes the removed snaps' own mount and service units in
/etc/systemd/system (snapd's state, not configuration; SNAP_ETC_UNITS).

RECIPE is the step's version: it is part of a slimmed source's generation (SUFFIX), so
a change here re-prepares every slimmed source, and rows of slim-sizes.tsv measured on
another recipe aren't taken (room.py)."""

import json
import re

RECIPE = 1
SUFFIX = f"+slim{RECIPE}"
FULL_FAT = ("ubuntu26-luks", "kinoite44", "garuda")
# copland's, cage's and czerny's cmp-run.sh --ceiling (DISK MEM, GiB) on 2026-11-24/25.
SMALL_CEILING = (9.0, 10.1)
EPS = 1e-9

# What the script never removes or changes (its guard refuses these, relative to a root).
NEVER = ("/etc", "/boot", "/efi", "/home", "/root", "/srv", "/var/srv", "/var/home", "/var/lib/abroot/etc",
         "/var/log/bko-holt", "/usr/share/grub", "/usr/share/plymouth", "/usr/lib/modules", "/lib/modules",
         "/usr/src", "/usr/lib/kernel", "/usr/lib/grub", "/usr/lib/systemd/boot", "/var/lib/initramfs-tools",
         "/usr/lib/firmware/amd-ucode", "/usr/lib/firmware/intel-ucode", "/lib/firmware/amd-ucode",
         "/lib/firmware/intel-ucode")
OLD_KERNELS = ("not removed: an old kernel is in grub.cfg's menu (the dump's boot.grub.grub_cfg.entries), "
               "and load's update-initramfs -u -k all and update-grub run over every installed kernel, so "
               "taking one away means rewriting the boot chain (apt-get purge's update-grub) or leaving "
               "entries and initramfs-tools state for a kernel that isn't there")
SNAPSHOTS = ("a btrfs snapshot (anduinos2's read-only deployment of its root) is left as it is, so it keeps "
             "what the live root loses: slimming it (ro false, the steps, ro true) leaves it last written after "
             "the snapshots made after it, and load then restores it after them, with another subvolume id "
             "(judge: storage.btrfs.0.subvolumes.6.id 262 -> 264, case anduinos2-b6082ebacc9aac0c, 2026-11-25), "
             "where the unslimmed source's same case passes")
# Filesystems whose df used isn't their live data, so the script's used() counts the du
# -sxk of their mount instead (each device once), with why: slim.json's used_du and notes.
# (Of the slimmed sources' filesystems on 2026-11-25, only zorin18's nilfs2 root.  btrfs keeps
# df: what a snapshot holds is still its data (SNAPSHOTS), and a deletion's extents are freed
# at the commit sync makes; f2fs counts valid blocks only, so a deletion is out of df at once;
# no slimmed source has bcachefs or ZFS, whose datasets df's /dev/ filter doesn't count.)
DU_FS = {"nilfs2": "log-structured: its df counts the blocks its checkpoints still hold until nilfs_cleanerd "
                   "reclaims them (protection period 1 h by default), and every deletion appends log, so the "
                   "slim step's df grew (zorin18, 2026-11-25: 7998 -> 8102 MiB with 2.43 GB freed by du); "
                   "its checkpoints are left as they are"}
SNAP_ETC_UNITS = ("snap remove deletes the removed snaps' mount and service units in /etc/systemd/system "
                  "(snapd's own state for the snaps it removes)")

# The steps, in order: (name, per root, what it removes).  "per root": run on each root
# the source boots (ABRoot's two), else once.  Never on a btrfs snapshot (SNAPSHOTS).
STEPS = (
    ("pkgcache", False, "package caches and package indexes: apt-get clean and /var/lib/apt/lists, pacman -Scc, "
                        "eopkg delete-cache, dnf/zypper/xbps clean, PackageKit's and snapd's download caches, "
                        "Slackware's /var/cache/packages"),
    ("logs", False, "journalctl --rotate and --vacuum-time=1s (the archived journals), rotated logs in /var/log, "
                    "other logs over 1 MiB truncated, coredumps and crash reports"),
    ("flatpak", False, "flatpak uninstall --system --all, then --unused: every system app and runtime"),
    ("snap", False, "snap remove --purge of every snap, snapd last"),
    ("abroot", False, "ABRoot's cached OCI image (/var/lib/abroot/storage), which only the next upgrade reads"),
    ("locale", True, "/usr/share/locale, locale-langpack and Qt's translations but C, en and the configured LANG's; "
                     "compiled locales in /usr/lib/locale/NAME/ but those"),
    ("docs", True, "/usr/share/doc, man, info, help, help-langpack, gtk-doc (Slackware: /usr/doc, /usr/man, /usr/info)"),
    ("themes", True, "icon themes, wallpapers, backgrounds and sound themes but the ones the system's settings name "
                     "(and what they inherit) and hicolor, Adwaita, breeze"),
    ("fonts", True, "CJK fonts and Noto's script and display families; the basic Latin set and fc-match's picks "
                    "stay; fc-cache -f"),
    ("firmware", True, "device firmware in /usr/lib/firmware (or /lib/firmware) but CPU microcode and the wireless "
                       "regulatory database: QEMU's virtual hardware loads none"),
)

SCRIPT = r'''# BackShot2's slim step (testvm/backshot2/bs2lib/slim.py, recipe @RECIPE@) for @SOURCE@:
# run by ht1.sh as root in the source's preparation overlay (HT_SOURCE_PREP), before
# its dump.  Removes only bulk that layout-drift testing doesn't need; never the
# layout, the boot chain, /etc, /home, /srv or anything content recipes plant.  Its
# last line is REBOOT: ht1.sh boots the slimmed source once more from cold.
set -u
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin:$PATH
ERR=0 R="" OTHER="" HOW="" MEASURE=""
say() { echo "SLIM $*"; }
err() { say "error $*"; ERR=1; }
# KiB on disk of the paths given (du -sxk, each once), 0 for none.
kib() { du -sxkc "$@" 2>/dev/null | tail -n 1 | cut -f1 | grep -E '^[0-9]+$' || echo 0; }
# The real filesystems whose df used isn't their live data (DU_FS): "DEVICE FSTYPE MOUNT",
# each device once, at its first mount (not a bind's DEVICE[/SUBDIR]; findmnt's \x20 a space).
dufs() {
    findmnt -rn -t @DU_FS@ -o SOURCE,FSTYPE,TARGET 2>/dev/null |
        awk '$1 ~ /^\/dev\// && index($1, "[") == 0 && !seen[$1]++' | sed 's/\\x20/ /g'
}
# The KiB the real filesystems use, each device once: df's used, but for dufs()' the du
# of its mount.
used() {
    { dufs | while read -r ud uf ut; do echo "$ud $(kib "$ut")"; done
      df -kP 2>/dev/null | awk 'NR > 1 && $1 ~ /^\/dev\// { print $1, $3 }'
    } | awk '!seen[$1]++ { s += $2 } END { print s + 0 }'
}
# Whether PATH (its real path, under root R) is one the step may never touch.
never() {
    local np
    np=$(readlink -f -- "$1" 2>/dev/null) || np=$1
    [ -z "$R" ] || np=${np#"$R"}
    case $np in
        @NEVER_CASE@) return 0 ;;
    esac
    return 1
}
RMOPT=--one-file-system; rm -rf --one-file-system /nonexistent-bko-slim 2>/dev/null || RMOPT=""
gone() {  # PATH...: removed, unless never() says no
    local gp
    for gp in "$@"; do
        [ -e "$gp" ] || [ -L "$gp" ] || continue
        if never "$gp"; then say "refused $gp"; continue; fi
        rm -rf $RMOPT -- "$gp" 2>/dev/null && continue
        # (What --one-file-system leaves is a mount point beneath, a live mount of the
        # running system's own, e.g. containers storage's graph/overlay: kept, empty.)
        if findmnt -rn -o TARGET 2>/dev/null | awk -v p="$gp" '$0 == p || index($0, p "/") == 1 { f = 1 } END { exit !f }'; then
            say "note kept the mount points under $gp"
        else
            err "rm $gp"
        fi
    done
}
# DIR KEEP...: DIR's entries removed but those named KEEP (shell patterns), DIR kept.
emptied() {
    local ed ee eb ek pat
    ed=$1; shift
    [ -d "$ed" ] && [ ! -L "$ed" ] || return 0
    if never "$ed"; then say "refused $ed"; return 0; fi
    for ee in "$ed"/* "$ed"/.[!.]*; do
        [ -e "$ee" ] || [ -L "$ee" ] || continue
        eb=${ee##*/} ek=0
        for pat in "$@"; do case $eb in $pat) ek=1; break ;; esac; done
        [ $ek = 1 ] || gone "$ee"
    done
}
# A step: NAME and its function, over MEASURE's paths (under R), measured before and after.
run() {
    local name body b a mp
    name=$1 body=$2
    set --; for mp in $MEASURE; do set -- "$@" "$R$mp"; done
    b=$(kib "$@"); HOW=""
    $body
    a=$(kib "$@")
    say "step $name ${R:-/} $((b - a)) ${HOW:-nothing to do}"
}
how() { HOW="${HOW:+$HOW; }$*"; }

# ---- the steps ----------------------------------------------------------------------
pkgcache() {
    local d
    if command -v apt-get >/dev/null && [ -f /var/lib/dpkg/status ]; then
        # (apt-get clean waits for apt's lock: a boot's own apt jobs may hold it a while.)
        n=0
        until out=$(apt-get -o DPkg::Lock::Timeout=60 clean 2>&1); do
            n=$((n + 1)); [ $n -lt 10 ] || break; sleep 15
        done
        [ $n -lt 10 ] && how "apt-get clean" || err "pkgcache: apt-get clean: $(echo "$out" | tail -n 1)"
        [ -d /var/lib/apt/lists ] && emptied /var/lib/apt/lists lock partial && how "/var/lib/apt/lists/*"
    fi
    if command -v pacman >/dev/null && [ -d /var/cache/pacman/pkg ]; then
        printf 'y\ny\n' | pacman -Scc >/dev/null 2>&1; emptied /var/cache/pacman/pkg && how "pacman -Scc"
    fi
    if command -v eopkg >/dev/null && [ -d /var/lib/eopkg ]; then eopkg delete-cache >/dev/null 2>&1 && how "eopkg delete-cache" || err "pkgcache: eopkg delete-cache"; fi
    if command -v dnf >/dev/null && [ -d /var/lib/dnf ]; then dnf clean all >/dev/null 2>&1 && how "dnf clean all"; fi
    if command -v zypper >/dev/null && [ -d /var/cache/zypp ]; then zypper --non-interactive clean --all >/dev/null 2>&1 && how "zypper clean --all"; fi
    if command -v xbps-remove >/dev/null && [ -d /var/db/xbps ]; then xbps-remove -Oy >/dev/null 2>&1 && how "xbps-remove -O"; fi
    for d in /var/cache/PackageKit /var/lib/snapd/cache /var/cache/packages /var/cache/eopkg/packages; do
        [ -d "$d" ] && [ -n "$(ls -A "$d" 2>/dev/null)" ] && emptied "$d" && how "$d/*"
    done
}
logs() {
    local d
    if command -v journalctl >/dev/null; then
        journalctl --rotate >/dev/null 2>&1; journalctl --vacuum-time=1s >/dev/null 2>&1 && how "journalctl --rotate --vacuum-time=1s"
    fi
    if [ -d /var/log ]; then
        find /var/log -xdev -type f \( -name '*.gz' -o -name '*.xz' -o -name '*.zst' -o -name '*.bz2' -o -name '*.old' -o -name '*.[0-9]' \) \
            ! -path '/var/log/journal/*' ! -path '/var/log/bko-holt/*' -exec rm -f {} + && how "rotated logs"
        find /var/log -xdev -type f -size +1024k ! -path '/var/log/journal/*' ! -path '/var/log/bko-holt/*' \
            -exec truncate -s 0 {} + && how "logs over 1 MiB truncated"
    fi
    for d in /var/lib/systemd/coredump /var/crash; do emptied "$d"; done
}
flatpak_() {
    command -v flatpak >/dev/null || return 0
    [ -n "$(flatpak list --system --columns=ref 2>/dev/null)" ] || return 0
    flatpak uninstall --system --all -y --noninteractive >/dev/null 2>&1 || err "flatpak: uninstall --all"
    flatpak uninstall --system --unused -y --noninteractive >/dev/null 2>&1
    how "flatpak uninstall --system --all, --unused"
}
snap_() {
    local n left s
    command -v snap >/dev/null || return 0
    [ -n "$(snap list 2>/dev/null | awk 'NR > 1 { print $1 }')" ] || return 0
    n=0
    while [ $n -lt 6 ]; do
        left=$(snap list 2>/dev/null | awk 'NR > 1 && $1 != "snapd" { print $1 }')
        [ -n "$left" ] || break
        for s in $left; do snap remove --purge "$s" >/dev/null 2>&1; done
        n=$((n + 1))
    done
    left=$(snap list 2>/dev/null | awk 'NR > 1 && $1 != "snapd" { print $1 }')
    [ -z "$left" ] || err "snap: still installed: $(echo $left)"
    if snap list snapd >/dev/null 2>&1; then snap remove --purge snapd >/dev/null 2>&1 || err "snap: remove snapd"; fi
    how "snap remove --purge (every snap, snapd last)"; say "note @SNAP_ETC_UNITS@"
}
abroot_() {
    [ -n "$OTHER" ] && [ -d /var/lib/abroot/storage ] || return 0
    emptied /var/lib/abroot/storage && how "/var/lib/abroot/storage/* (no ABRoot tool prunes it)"
}
# The languages kept: C, en, and the configured LANG's (LL: its xx_YY, L2: its xx).
LL="" L2=""
for f in /etc/locale.conf /etc/default/locale /etc/profile.d/lang.sh; do
    l=$(sed -n 's/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}LANG=["'"'"']\{0,1\}\([A-Za-z_@.0-9-]*\).*/\2/p' "$f" 2>/dev/null | head -n 1)
    [ -n "$l" ] || continue
    LL=${l%%.*} L2=${l%%_*}; say "note configured LANG $l: its locales kept"; break
done
case $L2 in ""|C|POSIX|en) LL="" L2="" ;; esac
LANGS="C C.* POSIX en en_* en@* locale.alias ${LL:+$LL $LL.* $LL@*} ${L2:+$L2 ${L2}@*}"
locale() {
    local d f b k
    emptied "$R/usr/share/locale" $LANGS && [ -d "$R/usr/share/locale" ] && how "/usr/share/locale/*"
    [ -d "$R/usr/share/locale-langpack" ] && emptied "$R/usr/share/locale-langpack" $LANGS && how "/usr/share/locale-langpack/*"
    for d in "$R/usr/share/qt5/translations" "$R/usr/share/qt6/translations" "$R/usr/share/qt/translations"; do
        [ -d "$d" ] && [ ! -L "$d" ] || continue
        for f in "$d"/*.qm; do
            [ -e "$f" ] || continue
            b=${f##*/}; b=${b%.qm}; k=0
            case $b in *_en|*_en_??) k=1 ;; esac
            [ -z "$LL" ] || case $b in *_"$LL"|*_"$L2") k=1 ;; esac
            [ $k = 1 ] || gone "$f"
        done; how "${d#"$R"}/*.qm"
    done
    if [ -d "$R/usr/lib/locale" ] && [ -n "$(find "$R/usr/lib/locale" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)" ]; then
        emptied "$R/usr/lib/locale" locale-archive C.utf8 C.UTF-8 $LANGS && how "/usr/lib/locale/NAME/"
    fi
}
docs() {
    local d
    for d in usr/share/doc usr/share/man usr/share/info usr/share/help usr/share/help-langpack usr/share/gtk-doc usr/doc usr/man usr/info; do
        [ -d "$R/$d" ] && [ ! -L "$R/$d" ] && [ -n "$(ls -A "$R/$d" 2>/dev/null)" ] && emptied "$R/$d" && how "/$d/*"
    done
}
# Every word of the settings that name themes (icons, cursors, wallpapers, sounds), one a line.
theme_words() {
    local f
    for f in "$R"/usr/share/glib-2.0/schemas/*.override "$R"/usr/share/glib-2.0/schemas/*.gschema.xml \
        "$R"/etc/dconf/db/*.d/* "$R"/etc/gtk-*/settings.ini "$R"/etc/xdg/kdeglobals "$R"/etc/xdg/*rc \
        "$R"/usr/share/plasma/look-and-feel/*/contents/defaults "$R"/etc/sddm.conf "$R"/etc/sddm.conf.d/* \
        "$R"/usr/lib/sddm/sddm.conf.d/* "$R"/etc/lightdm/*.conf "$R"/etc/lightdm/*.conf.d/* \
        "$R"/usr/share/lightdm/*.conf.d/* "$R"/usr/share/icons/default/index.theme "$R"/etc/environment \
        "$R"/etc/X11/Xresources "$R"/etc/X11/Xresources/* "$R"/usr/share/sddm/themes/*/theme.conf* \
        "$R"/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/*.xml "$R"/etc/xdg/xfce4/*/*.xml \
        "$R"/usr/share/gnome-background-properties/*.xml "$R"/usr/share/mate-background-properties/*.xml \
        "$R"/usr/share/cinnamon-background-properties/*.xml "$R"/etc/skel/.config/*rc "$R"/etc/skel/.config/gtk-*/settings.ini; do
        [ -f "$f" ] && cat "$f" 2>/dev/null
    done | tr -c 'A-Za-z0-9_.+-' '\n' | grep . | sort -u
    readlink -f "$R/etc/alternatives/x-cursor-theme" 2>/dev/null | tr '/' '\n'
}
themes() {
    local W K KF t top f
    W=$(mktemp); theme_words > "$W"
    # Icon and cursor themes: the ones named, then what they inherit, a few rounds.
    K=$(mktemp); printf '%s\n' hicolor locolor default Adwaita AdwaitaLegacy breeze breeze-dark breeze_cursors gnome > "$K"
    for t in "$R"/usr/share/icons/*/; do t=${t%/}; t=${t##*/}; grep -qxF -- "$t" "$W" && echo "$t" >> "$K"; done
    for _ in 1 2 3 4; do
        for t in $(sort -u "$K"); do
            sed -n 's/^Inherits=//p' "$R/usr/share/icons/$t/index.theme" "$R/usr/share/icons/$t/cursor.theme" 2>/dev/null | tr ',' '\n' | sed 's/^ *//; s/ *$//' >> "$K"
        done
    done
    if [ -d "$R/usr/share/icons" ] && [ ! -L "$R/usr/share/icons" ]; then
        for t in "$R"/usr/share/icons/*; do
            [ -d "$t" ] && [ ! -L "$t" ] || continue
            grep -qxF -- "${t##*/}" "$K" || gone "$t"
        done; how "icon themes but $(for t in $(sort -u "$K"); do [ -d "$R/usr/share/icons/$t" ] && printf '%s,' "$t"; done)"
    fi
    # Wallpapers and backgrounds: a file stays when the settings name it (or a link to it
    # does), or it is inside a wallpaper package (metadata.json/.desktop) they name.
    for top in "$R/usr/share/backgrounds" "$R/usr/share/wallpapers"; do
        [ -d "$top" ] && [ ! -L "$top" ] || continue
        KF=$(mktemp)
        find "$top" \( -type f -o -type l \) 2>/dev/null | while read -r f; do
            b=${f##*/} keep=0
            grep -qxF -- "$b" "$W" && keep=1
            rel=${f#"$top"/}; dir=${rel%/*}
            while [ "$keep" = 0 ] && [ "$dir" != "$rel" ] && [ -n "$dir" ]; do
                n=${dir##*/}
                if grep -qxF -- "$n" "$W" && { [ -e "$top/$dir/metadata.json" ] || [ -e "$top/$dir/metadata.desktop" ]; }; then keep=1; fi
                [ "$dir" = "${dir%/*}" ] && break; dir=${dir%/*}
            done
            if [ "$keep" = 1 ]; then echo "$f"; readlink -f -- "$f"; fi
        done | sort -u > "$KF"
        find "$top" \( -type f -o -type l \) 2>/dev/null | while read -r f; do
            grep -qxF -- "$f" "$KF" || grep -qxF -- "$(readlink -f -- "$f")" "$KF" || gone "$f"
        done
        find "$top" -mindepth 1 -depth -type d -empty -delete 2>/dev/null
        how "${top#"$R"} but $(wc -l < "$KF") named"; rm -f "$KF"
    done
    # Sound themes: freedesktop's and the named ones.
    if [ -d "$R/usr/share/sounds" ] && [ ! -L "$R/usr/share/sounds" ]; then
        for t in "$R"/usr/share/sounds/*; do
            [ -d "$t" ] && [ ! -L "$t" ] || continue
            case ${t##*/} in freedesktop|default|alsa) continue ;; esac
            grep -qxF -- "${t##*/}" "$W" || gone "$t"
        done; how "sound themes but freedesktop and the named"
    fi
    rm -f "$W" "$K"
}
# fc-match's picks on the booted root (real paths, relative to it): never removed, on any root.
PICKS=$(mktemp)
if command -v fc-match >/dev/null; then
    for f in sans-serif serif monospace emoji "DejaVu Sans" "DejaVu Serif" "DejaVu Sans Mono"; do
        fc-match -f '%{file}\n' "$f" 2>/dev/null
    done | while read -r f; do readlink -f -- "$f"; done > "$PICKS"
fi
fonts() {
    local d f rf
    d="$R/usr/share/fonts"; [ -d "$d" ] && [ ! -L "$d" ] || return 0
    find "$d" -type f \( -iname '*cjk*' -o -iname 'wqy*' -o -iname 'ukai*' -o -iname 'uming*' -o -iname '*sazanami*' -o -iname '*takao*' \
        -o \( -name 'Noto*' ! -name 'NotoSans-*' ! -name 'NotoSansMono-*' ! -name 'NotoSerif-*' ! -name 'NotoMono-*' ! -name 'NotoColorEmoji*' \) \) 2>/dev/null |
        while read -r f; do rf=$(readlink -f -- "$f"); [ -z "$R" ] || rf=${rf#"$R"}; grep -qxF -- "$rf" "$PICKS" || gone "$f"; done
    find "$d" -mindepth 1 -depth -type d -empty -delete 2>/dev/null
    how "CJK and Noto's script and display families"
    if [ -z "$R" ] && command -v fc-cache >/dev/null; then fc-cache -f >/dev/null 2>&1 && how "fc-cache -f"; fi
}
firmware() {
    local d rd
    for d in "$R/usr/lib/firmware" "$R/lib/firmware"; do
        [ -d "$d" ] && [ ! -L "$d" ] || continue
        rd=$(readlink -f -- "$d")
        case " $FWDONE " in *" $rd "*) continue ;; esac
        FWDONE="$FWDONE $rd"
        emptied "$d" 'amd-ucode*' 'intel-ucode*' 'regulatory.db*' && how "${d#"$R"}/* but microcode and regulatory.db"
    done
}

# ---- the roots ----------------------------------------------------------------------
say "recipe @RECIPE@ @SOURCE@"
# (Its boot settled first, so that the state before is comparable with the one after.)
command -v systemctl >/dev/null && timeout 300 systemctl is-system-running --wait >/dev/null 2>&1
say "state before $(systemctl is-system-running 2>/dev/null || true)"
say "failed before $(systemctl --failed --plain --no-legend 2>/dev/null | awk '{ print $1 }' | tr '\n' ' ')"
dufs | while read -r ud uf ut; do say "usedby du $ud $uf $ut"; done
say "used before $(used)"
RO="" ACT=""
if [ -f /usr/share/abroot/abroot.json ] && command -v abroot >/dev/null; then
    # ABRoot: both roots are slimmed (the source phase boots each); the present one is
    # mounted read-only and the other not at all, and both are left as they were.
    dev=$(findmnt -vno SOURCE /)
    vglv=$(lvs --noheadings -o vg_name,lv_name "$dev" 2>/dev/null | awk '{ print $1 "/" $2 }')
    case $vglv in
        */root-a) olv=${vglv%a}b ;;
        */root-b) olv=${vglv%b}a ;;
        *) olv="" ;;
    esac
    if [ -z "$olv" ]; then
        err "abroot: no other root beside $dev ($vglv)"
    else
        if [ ! -b "/dev/$olv" ]; then lvchange -ay "$olv" && ACT=$olv || err "abroot: lvchange -ay $olv"; fi
        for m in / /usr; do
            if findmnt -no OPTIONS "$m" 2>/dev/null | tr ',' '\n' | grep -qx ro; then
                mount -o remount,rw "$m" && RO="$RO $m" || err "abroot: remount $m rw"
            fi
        done
        mkdir -p /run/bko-slim/other; OTHER=$(readlink -f /run/bko-slim/other)
        mount "/dev/$olv" "$OTHER" || { err "abroot: mount /dev/$olv"; OTHER=""; }
        say "note abroot: the present root ($vglv) and the other ($olv) both slimmed"
    fi
fi

MEASURE="/var/cache /var/lib/apt/lists /var/lib/snapd/cache"; run pkgcache pkgcache
MEASURE="/var/log /var/lib/systemd/coredump /var/crash"; run logs logs
MEASURE="/var/lib/flatpak"; run flatpak flatpak_
MEASURE="/var/lib/snapd /snap"; run snap snap_
MEASURE="/var/lib/abroot/storage"; run abroot abroot_
for R in "" ${OTHER:+"$OTHER"}; do
    MEASURE="/usr/share/locale /usr/share/locale-langpack /usr/share/qt5/translations /usr/share/qt6/translations /usr/share/qt/translations /usr/lib/locale"; run locale locale
    MEASURE="/usr/share/doc /usr/share/man /usr/share/info /usr/share/help /usr/share/help-langpack /usr/share/gtk-doc /usr/doc /usr/man /usr/info"; run docs docs
    MEASURE="/usr/share/icons /usr/share/backgrounds /usr/share/wallpapers /usr/share/sounds"; run themes themes
    MEASURE="/usr/share/fonts"; run fonts fonts
    FWDONE=""; MEASURE="/usr/lib/firmware /lib/firmware"; run firmware firmware
done
R=""
sync
rm -f "$PICKS"
if [ -n "$OTHER" ]; then umount "$OTHER" || err "abroot: umount $OTHER"; rmdir "$OTHER" "${OTHER%/*}" 2>/dev/null; fi
[ -z "$ACT" ] || lvchange -an "$ACT" || err "abroot: lvchange -an $ACT"
# (The present root stays read-write until the reboot below mounts it read-only again:
# a remount read-only is refused while the step's deletions are still being written.)
[ -z "$RO" ] || say "note abroot: $RO read-write until the reboot"
say "used after $(used)"
[ "$ERR" = 0 ] || { echo "slim: failed (its SLIM error lines)"; exit 1; }
echo REBOOT
'''


def _never_case():
    """NEVER as one sh case pattern list (each path and anything under it)."""
    return "|".join(f"{p}|{p}/*" for p in NEVER)


def script(source):
    """The slim step's sh script for SOURCE (ht1.sh's HT_SOURCE_PREP)."""
    return (SCRIPT.replace("@RECIPE@", str(RECIPE)).replace("@SOURCE@", source)
            .replace("@NEVER_CASE@", _never_case()).replace("@SNAP_ETC_UNITS@", SNAP_ETC_UNITS)
            .replace("@DU_FS@", ",".join(DU_FS)))


def full_over_small(source, path=None):
    """Whether SOURCE's full room (ram-sizes.tsv's, room.full_of()) is over SMALL_CEILING."""
    from . import room as RM
    d, m = RM.full_of(source) if path is None else RM.full_of(source, path)
    return d > SMALL_CEILING[0] + EPS or m > SMALL_CEILING[1] + EPS


def slimmed(source, path=None):
    """Whether bs2 slims SOURCE: its full room is over the small hosts' ceiling, and it is
    neither one of FULL_FAT nor one of Alpine's four (bs2's own room) nor a BackShot1 run."""
    from . import room as RM
    if source in FULL_FAT or source in RM.KEEP or source.startswith("run:"):
        return False
    return full_over_small(source, path)


def suffix(source):
    """What a slimmed source's generation carries (SUFFIX), else ""."""
    return SUFFIX if slimmed(source) else ""


_LINE = re.compile(r"^SLIM (\S+) (.*)$")


def parse(text):
    """slim.json's body from the script's output (ht1.sh's logs/source-prep.txt)."""
    out = {"recipe": None, "source": None, "state_before": None, "failed_before": [], "used_before": None,
           "used_after": None, "used_du": [], "steps": [], "notes": [], "errors": [], "refused": []}
    for line in text.splitlines():
        m = _LINE.match(line.rstrip())
        if not m:
            continue
        kind, rest = m.groups()
        w = rest.split()
        if kind == "recipe" and w:
            out["recipe"] = int(w[0]) if w[0].isdigit() else w[0]
            out["source"] = w[1] if len(w) > 1 else None
        elif kind == "state" and w[:1] == ["before"]:
            out["state_before"] = w[1] if len(w) > 1 else ""
        elif kind == "failed" and w[:1] == ["before"]:
            out["failed_before"] = w[1:]
        elif kind == "used" and len(w) >= 2 and w[1].isdigit():
            out["used_" + w[0]] = int(w[1]) * 1024
        elif kind == "usedby" and w[:1] == ["du"] and len(w) >= 4:
            dev, fs, mnt = w[1], w[2], rest.split(None, 3)[3]
            out["used_du"].append({"device": dev, "fstype": fs, "mount": mnt})
            out["notes"].append(f"used: {fs} {dev} ({mnt}) measured by du -sxk of its mount, not df: "
                                + DU_FS.get(fs, "df's used isn't its live data"))
        elif kind == "step" and len(w) >= 3:
            try:
                freed = int(w[2]) * 1024
            except ValueError:
                continue
            out["steps"].append({"step": w[0], "root": w[1], "freed": freed, "how": " ".join(w[3:])})
        elif kind == "note":
            out["notes"].append(rest)
        elif kind == "error":
            out["errors"].append(rest)
        elif kind == "refused":
            out["refused"].append(rest)
    out["freed"] = sum(s["freed"] for s in out["steps"])
    return out


def record(text, bootstate_after, source):
    """slim.json for SOURCE's preparation: parse(TEXT) plus the boot state after its cold
    boot (ht1.sh's logs/source-bootstate.txt, its first line), what the step never
    touches, and old kernels' word.  Raises ValueError when the step didn't run as this
    recipe, or left a source that ran clean degraded."""
    r = parse(text)
    if r["recipe"] != RECIPE or r["source"] != source:
        raise ValueError(f"the slim step's output names recipe {r['recipe']} for {r['source']}, not {RECIPE} for {source}")
    if r["errors"]:
        raise ValueError(f"the slim step failed: {'; '.join(r['errors'][:3])}")
    after = (bootstate_after or "").strip().splitlines()[:1]
    r["state_after"] = after[0].strip() if after else None
    if r["state_before"] == "running" and r["state_after"] != "running":
        raise ValueError(f"the slimmed source boots {r['state_after'] or 'to no state'}, not running as before it was slimmed")
    r["never"] = list(NEVER)
    r["old_kernels"] = OLD_KERNELS
    return r


def dumps(r):
    return json.dumps(r, indent=1, sort_keys=True) + "\n"
