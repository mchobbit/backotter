#!/usr/bin/env bash
# ht1-bsd.sh's dump readers and script writers, on synthetic layouts: no
# VM, no root, no RAM disk.  Run it anywhere python3 and bash are:
#
#   ./ht1-bsd-test.sh
#
# It takes the functions out of ht1-bsd.sh itself (the script runs a round
# trip when it is run, so it can't be sourced) and calls them with the
# globals they read set as a run would set them.  The layouts are the GELI
# guests' (docs/design/geli.md, docs/design/geli2.md), small by hand:
# freebsd15-geli-bios (ZFS root on GELI, onetime swap) and
# freebsd15-geli-ufs (UFS root on GELI with key slot 1, and /data on a
# provider a key file alone opens, that key file on the encrypted root).
# One "ok" or "FAIL" line a case; exit 1 if any case FAILs.
set -uo pipefail
HERE=$(dirname "$(realpath "$0")")
BSD=$HERE/ht1-bsd.sh
[[ -r $BSD ]] || { echo "ht1-bsd-test: no ht1-bsd.sh beside $0" >&2; exit 2; }

# The functions under test, by name: each from its "name() {" to the next
# line that is a "}" of its own (none of their heredocs has one).
want=(geli_provs sr_vols geli_fstab_mp onetime_swaps geli_files_list geli_slot1_script mount_script umount_script log_panicked wait_why vm_wait resc_up backup_failed put list_tree list_tree_watched list_poll_sh list_diag_sh)
src=""
for f in "${want[@]}"; do
    one=$(sed -n "/^$f() {/,/^}$/p" "$BSD")
    [[ -n $one && $(tail -n 1 <<<"$one") == "}" ]] || { echo "ht1-bsd-test: can't take $f out of ht1-bsd.sh" >&2; exit 2; }
    src+=$one$'\n'
done
eval "$src" || { echo "ht1-bsd-test: the functions don't parse" >&2; exit 2; }

# The globals those functions read, as a FreeBSD run sets them: the rescuer's
# own disk is vtbd0, the source's (or the target's) vtbd1, the decoy vtbd2.
# (PLAT, RN and GELI_DIR are read by the functions eval'd above; the checker
# can't see into an eval, hence the disables.)
# shellcheck disable=SC2034
PLAT=freebsd
# shellcheck disable=SC2034
RN=(vtbd0 vtbd1 vtbd2)
# shellcheck disable=SC2034
GELI_DIR=/tmp/ht1-geli GELI_KEY=/tmp/ht1-geli/passphrase GELI_KEY1=/tmp/ht1-geli/passphrase-slot1
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

ncase=0 nfail=0
chk() { local what=$1; shift
    ncase=$((ncase + 1))
    if "$@" >/dev/null 2>&1; then echo "ok   $what"; else echo "FAIL $what"; nfail=$((nfail + 1)); fi
}
# A file's lines, for the checks below: hasx an exact line, has a regexp,
# before A's first line earlier than B's.
hasx() { grep -qxF "$2" "$1"; }
has() { grep -qE "$2" "$1"; }
no() { ! grep -qE "$2" "$1"; }
before() {
    local a b
    a=$(grep -n -E "$2" "$1" | head -n 1 | cut -d: -f1)
    b=$(grep -n -E "$3" "$1" | head -n 1 | cut -d: -f1)
    [[ -n $a && -n $b ]] && ((a < b))
}

# ---- the layouts ------------------------------------------------------

# freebsd15-geli-ufs: p1 the ESP, p2 plain swap, p3 the UFS root on GELI
# (keys 0 and 1, a passphrase), p4 /data on GELI with authentication, opened
# by /etc/geli/p4.key alone -- a file on p3's own file system.  KEYED and
# KEYFILES override p4's, for the cases that aren't the guest's.
mk_ufs() { # FILE [KEYED] [KEYFILES-JSON] [P4-MOUNT]
    python3 - "$1" "${2:-keyfile}" "${3:-[\"/etc/geli/p4.key\"]}" "${4:-/data}" <<'PY'
import json, sys
out, keyed, kfs, p4mp = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]
def prov(name, keys, keyed, kfs, aalgo):
    return {"version": 7, "flags": ["BOOT", "GELIBOOT"], "ealgo": "AES-XTS", "keylen": 256,
            "aalgo": aalgo, "provsize": 1 << 30, "sectorsize": 4096, "keys": keys,
            "iterations": 3031038, "provider": name + ".eli", "attached": True, "onetime": False,
            "keyed_by": keyed, "keyfiles": kfs, "content": {"type": "ufs", "usage": "filesystem"}}
d = {"os": {"family": "freebsd", "id": "freebsd"}, "storage": {
    "disks": [{"name": "vtbd0", "size_bytes": 16 << 30, "partitions": [
        {"name": "vtbd0p1", "content": {"type": "vfat", "usage": "filesystem"}},
        {"name": "vtbd0p2", "content": None},
        {"name": "vtbd0p3", "content": {"type": "geli", "usage": "crypto"},
         "geli": prov("vtbd0p3", [0, 1], "passphrase", [], None)},
        {"name": "vtbd0p4", "content": {"type": "geli", "usage": "crypto"},
         "geli": prov("vtbd0p4", [0], keyed, kfs, "HMAC/SHA256")},
    ]}],
    "zfs": {"pools_from_labels": [], "zpool_cache": None, "tools": None},
    "fstab": [
        {"spec": "/dev/vtbd0p3.eli", "file": "/", "type": "ufs", "options": "rw", "resolves_to": "vtbd0p3", "eli": True},
        {"spec": "/dev/gpt/efiboot0", "file": "/boot/efi", "type": "msdosfs", "options": "rw", "resolves_to": "vtbd0p1"},
        {"spec": "/dev/vtbd0p2", "file": "none", "type": "swap", "options": "sw", "resolves_to": "vtbd0p2"},
        {"spec": "/dev/vtbd0p4.eli", "file": p4mp, "type": "ufs", "options": "rw", "resolves_to": "vtbd0p4", "eli": True},
    ],
    "swaps": [{"device": "vtbd0p2", "onetime": False, "size_bytes": 1 << 30}],
}}
json.dump(d, open(out, "w"))
PY
}
# freebsd15-geli-bios: p1 freebsd-boot, p2 onetime swap (vtbd0p2.eli in
# fstab, no metadata on the partition), p3 the zroot pool on GELI.
mk_bios() { # FILE
    python3 - "$1" <<'PY'
import json, sys
d = {"os": {"family": "freebsd", "id": "freebsd"}, "storage": {
    "disks": [{"name": "vtbd0", "size_bytes": 16 << 30, "partitions": [
        {"name": "vtbd0p1", "content": None},
        {"name": "vtbd0p2", "content": None},
        {"name": "vtbd0p3", "content": {"type": "geli", "usage": "crypto"},
         "geli": {"version": 7, "flags": ["BOOT", "GELIBOOT"], "ealgo": "AES-XTS", "keylen": 256,
                  "aalgo": None, "provsize": 1 << 30, "sectorsize": 4096, "keys": [0],
                  "iterations": 3031038, "provider": "vtbd0p3.eli", "attached": True,
                  "onetime": False, "keyed_by": "passphrase", "keyfiles": [],
                  "content": {"type": "zfs_member", "usage": "filesystem", "pool_name": "zroot"}}},
    ]}],
    "zfs": {"pools_from_labels": [], "zpool_cache": None, "tools": {
        "imported_pools": [{"name": "zroot", "properties": {"guid": "7771", "bootfs": "zroot/ROOT/default"}, "vdevs": {}}],
        "datasets": [{"name": "zroot/ROOT/default", "mountpoint": "/", "mounted": "yes", "canmount": "noauto"}]}},
    "fstab": [{"spec": "/dev/vtbd0p2.eli", "file": "none", "type": "swap", "options": "sw", "resolves_to": "vtbd0p2", "eli": True}],
    "swaps": [{"device": "vtbd0p2", "onetime": True, "size_bytes": 2 << 30}],
}}
json.dump(d, open(sys.argv[1], "w"))
PY
}

mk_ufs "$T/ufs.json"
mk_bios "$T/bios.json"

# ---- geli_provs ------------------------------------------------------

geli_provs "$T/ufs.json" > "$T/provs-ufs.txt" 2>&1
chk "geli_provs: the -ufs guest's two providers, nothing else" eval '[[ $(grep -c . "$T/provs-ufs.txt") == 2 ]]'
chk "geli_provs: p3 passphrase, key slots 0 and 1, no key file" \
    hasx "$T/provs-ufs.txt" "vtbd0p3 vtbd0p3 passphrase 0,1 -"
chk "geli_provs: p4 keyfile, slot 0, /etc/geli/p4.key" \
    hasx "$T/provs-ufs.txt" "vtbd0p4 vtbd0p4 keyfile 0 /etc/geli/p4.key"
geli_provs "$T/bios.json" > "$T/provs-bios.txt" 2>&1
chk "geli_provs: the -bios guest's one provider, slot 0 by default" \
    eval 'same=$(grep -c . "$T/provs-bios.txt"); [[ $same == 1 ]]' \
    && chk "  its line" hasx "$T/provs-bios.txt" "vtbd0p3 vtbd0p3 passphrase 0 -"
# A record with no keys at all still says slot 0: nothing asks for slot 1.
python3 - "$T/ufs.json" "$T/nokeys.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for p in d["storage"]["disks"][0]["partitions"]:
    if "geli" in p:
        del p["geli"]["keys"]
json.dump(d, open(sys.argv[2], "w"))
PY
geli_provs "$T/nokeys.json" > "$T/provs-nokeys.txt" 2>&1
chk "geli_provs: a record with no keys field reads as slot 0" \
    eval '[[ $(awk "{print \$4}" "$T/provs-nokeys.txt" | sort -u | tr "\n" " ") == "0 " ]]'
# A key file whose path would be read as two fields is refused, not silently
# cut in half.
mk_ufs "$T/badkf.json" keyfile '["/etc/geli/p 4.key"]'
chk "geli_provs: a key file with white space in its path is refused" \
    eval '! geli_provs "$T/badkf.json" >"$T/provs-bad.txt" 2>&1'
chk "  and it says which path and why" has "$T/provs-bad.txt" "p 4.key.*white space"

# ---- mount_script: the keyfile provider's place -----------------------

mount_script "$T/ufs.json" 1 /mnt/src > "$T/mount-ufs.sh" 2>&1
chk "mount_script: the -ufs layout writes a script" eval '[[ -s "$T/mount-ufs.sh" ]]'
chk "mount_script: p3 attached read-only with the passphrase" \
    hasx "$T/mount-ufs.sh" "[ -c /dev/vtbd1p3.eli ] || geli attach -r -j $GELI_KEY /dev/vtbd1p3"
chk "mount_script: p4 attached read-only with the key file on the mounted root" \
    hasx "$T/mount-ufs.sh" "[ -c /dev/vtbd1p4.eli ] || geli attach -r -p -k /mnt/src/etc/geli/p4.key /dev/vtbd1p4"
chk "mount_script: p4's attach comes after the root is mounted" \
    before "$T/mount-ufs.sh" "mount -r -t ufs /dev/vtbd1p3\.eli /mnt/src\$" "geli attach -r -p -k"
chk "mount_script: and before /data on it is mounted" \
    before "$T/mount-ufs.sh" "geli attach -r -p -k" "mount -r -t ufs /dev/vtbd1p4\.eli /mnt/src/data\$"
chk "mount_script: both providers go in the attached list, for the detach" \
    eval 'hasx "$T/mount-ufs.sh" "echo /dev/vtbd1p3.eli >> $GELI_DIR/attached" &&
          hasx "$T/mount-ufs.sh" "echo /dev/vtbd1p4.eli >> $GELI_DIR/attached"'
chk "mount_script: p4's own .eli is checked after its attach" \
    has "$T/mount-ufs.sh" "^\[ -c /dev/vtbd1p4\.eli \] \|\| \{ echo 'geli attach /dev/vtbd1p4: no /dev/vtbd1p4\.eli'; exit 1; \}$"
chk "mount_script: the ESP is still mounted under the root" \
    hasx "$T/mount-ufs.sh" "mkdir -p /mnt/src/boot/efi 2>/dev/null || true; mount -r -t msdosfs /dev/vtbd1p1 /mnt/src/boot/efi"
chk "mount_script: slot 1's passphrase is never an attach's key here" no "$T/mount-ufs.sh" "passphrase-slot1"
chk "mount_script: nothing says the run has only the passphrase any more" no "$T/mount-ufs.sh" "only the passphrase"

# A provider keyed by both: the passphrase and the key file together, no -p.
mk_ufs "$T/both.json" both '["/etc/geli/p4.key"]'
mount_script "$T/both.json" 1 /mnt/src > "$T/mount-both.sh" 2>&1
chk "mount_script: keyed by both takes -j and -k, not -p" \
    hasx "$T/mount-both.sh" "[ -c /dev/vtbd1p4.eli ] || geli attach -r -j $GELI_KEY -k /mnt/src/etc/geli/p4.key /dev/vtbd1p4"

# Two key files: a -k each, in the record's order.
mk_ufs "$T/two.json" keyfile '["/etc/geli/a.key", "/etc/geli/b.key"]'
mount_script "$T/two.json" 1 /mnt/src > "$T/mount-two.sh" 2>&1
chk "mount_script: a -k for each of the record's key files" \
    hasx "$T/mount-two.sh" "[ -c /dev/vtbd1p4.eli ] || geli attach -r -p -k /mnt/src/etc/geli/a.key -k /mnt/src/etc/geli/b.key /dev/vtbd1p4"

# A key file the layout mounts nothing for, and a record that names none:
# the script says so and stops, instead of attaching with nothing.
mk_ufs "$T/nokf.json" keyfile '[]'
mount_script "$T/nokf.json" 1 /mnt/src > "$T/mount-nokf.sh" 2>&1
chk "mount_script: keyfile and no key file in the dump stops the script" \
    eval 'has "$T/mount-nokf.sh" "names no key file" && has "$T/mount-nokf.sh" "^echo .*; exit 1$"'
# A key file under no mount point of the layout's: a layout whose root this
# script doesn't mount (no fstab root, no pool) holds nothing.
mk_ufs "$T/offkf.json" keyfile '["/media/stick/p4.key"]'
python3 - "$T/offkf.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["storage"]["fstab"] = [e for e in d["storage"]["fstab"] if e["file"] != "/"]
json.dump(d, open(sys.argv[1], "w"))
PY
mount_script "$T/offkf.json" 1 /mnt/src > "$T/mount-offkf.sh" 2>&1
chk "mount_script: a key file under no mount point of the layout's stops the script" \
    has "$T/mount-offkf.sh" "/media/stick/p4\.key\) is not on one file system this script mounts"
chk "  and a key file the root holds is the root's, whatever its directory" \
    eval 'mk_ufs "$T/deep.json" keyfile "[\"/media/stick/p4.key\"]" &&
          mount_script "$T/deep.json" 1 /mnt/src | grep -qxF "[ -c /dev/vtbd1p4.eli ] || geli attach -r -p -k /mnt/src/media/stick/p4.key /dev/vtbd1p4"'
# The key file on the provider's own file system: the mount would need the
# attach that needs the mount.
mk_ufs "$T/self.json" keyfile '["/data/p4.key"]' /data
mount_script "$T/self.json" 1 /mnt/src > "$T/mount-self.sh" 2>&1
chk "mount_script: a key file on the provider's own file system stops the script" \
    has "$T/mount-self.sh" "its key file is under /data, which this script mounts later"
chk "  and it mounts nothing from that provider first" no "$T/mount-self.sh" "mount -r -t ufs /dev/vtbd1p4"

# ---- mount_script: the passphrase-only guests are as they were --------

mount_script "$T/bios.json" 1 /mnt/src > "$T/mount-bios.sh" 2>&1
chk "mount_script: the -bios layout attaches p3 with the passphrase" \
    hasx "$T/mount-bios.sh" "[ -c /dev/vtbd1p3.eli ] || geli attach -r -j $GELI_KEY /dev/vtbd1p3"
chk "mount_script: and imports zroot read-only by its GUID" \
    hasx "$T/mount-bios.sh" "zpool import -f -N -o readonly=on -R /mnt/src 7771"
chk "mount_script: the attach comes before the import" \
    before "$T/mount-bios.sh" "geli attach" "zpool import"
chk "mount_script: the onetime swap is not attached or mounted" no "$T/mount-bios.sh" "vtbd1p2"
chk "umount_script: the attached list is read back for the detach" \
    eval 'umount_script "$T/mount-bios-u" /mnt/src >/dev/null 2>&1; umount_script "$T/bios.json" /mnt/src | grep -qF "geli detach"'

# ---- geli_slot1_script ------------------------------------------------

geli_slot1_script "$T/ufs.json" 1 > "$T/slot1.sh" 2>&1
chk "geli_slot1_script: slot 1 tried on p3, read-only, with slot 1's file alone" \
    hasx "$T/slot1.sh" "if geli attach -r -n 1 -j $GELI_KEY1 /dev/vtbd1p3; then"
chk "geli_slot1_script: slot 0's passphrase is never given to -n 1" \
    no "$T/slot1.sh" "\-j $GELI_KEY "
chk "geli_slot1_script: an attached provider is detached first" \
    hasx "$T/slot1.sh" "[ ! -c /dev/vtbd1p3.eli ] || geli detach /dev/vtbd1p3.eli || echo 'HT1-GELI-SLOT1 vtbd0p3 busy'"
chk "geli_slot1_script: it detaches again and says ok" \
    hasx "$T/slot1.sh" "  geli detach /dev/vtbd1p3.eli && echo 'HT1-GELI-SLOT1 vtbd0p3 ok' || echo 'HT1-GELI-SLOT1 vtbd0p3 no-detach'"
chk "geli_slot1_script: and no, where it won't open" hasx "$T/slot1.sh" "  echo 'HT1-GELI-SLOT1 vtbd0p3 no'"
chk "geli_slot1_script: nothing for p4, whose slot 1 isn't in use" no "$T/slot1.sh" "vtbd0p4|vtbd1p4"
geli_slot1_script "$T/bios.json" 1 > "$T/slot1-bios.sh" 2>&1
chk "geli_slot1_script: no command at all where no slot 1 is in use" \
    eval '[[ $(grep -c . "$T/slot1-bios.sh") == 0 ]] && [[ -z $(geli_slot1_script "$T/bios.json" 1) ]]'
# The script runs as a shell script, whatever it would do to a real provider.
chk "geli_slot1_script: what it writes parses as a shell script" bash -n "$T/slot1.sh"
chk "mount_script: what it writes parses as a shell script" eval 'bash -n "$T/mount-ufs.sh" && bash -n "$T/mount-bios.sh"'

# ---- the verify's other readers ---------------------------------------

chk "geli_fstab_mp: p4's mount point" eval '[[ $(geli_fstab_mp "$T/ufs.json" vtbd0p4) == /data ]]'
chk "geli_fstab_mp: p3's is the root" eval '[[ $(geli_fstab_mp "$T/ufs.json" vtbd0p3) == / ]]'
chk "geli_fstab_mp: a swap on a provider has none (the -bios guest's p2)" \
    eval '[[ -z $(geli_fstab_mp "$T/bios.json" vtbd0p2) ]]'
chk "geli_fstab_mp: a partition fstab doesn't name has none" \
    eval '[[ -z $(geli_fstab_mp "$T/ufs.json" vtbd0p9) ]]'
chk "onetime_swaps: the -bios guest's p2" eval '[[ $(onetime_swaps "$T/bios.json") == vtbd0p2 ]]'
chk "onetime_swaps: the -ufs guest's plain swap is not one" eval '[[ -z $(onetime_swaps "$T/ufs.json") ]]'
chk "geli_files_list: paths relative to the mount point, and their SHA-256" \
    eval '[[ $(geli_files_list /mnt/src/data) == "cd /mnt/src/data && find . -type f -exec sha256 -r {} + | LC_ALL=C sort" ]]'

# ---- the rescuer's boot -----------------------------------------------
# log_panicked on consoles as they were, and resc_up with vm_up, sshk and
# ht_qemus stubbed: a "VM" is its console file, and sshd answers once BOOTS
# boots were made (UP_AT), which is what vm_wait asks.

printf '%s\r\n' "Building databases: dev, utmp, utmpx." "[   2.3630987] panic: cpu1: time has not advanced in 1501 heartbeats" > "$T/nb-panic.log"
printf '%s\r\n' "Setting sysctl variables:" "ddb.onpanic: 1 -> 0" "savecore: reboot after panic: page fault" "Nov 18 07:05:33 netbsd11 poweroff: powered off by root" > "$T/nb-fine.log"
printf '%s\n' "panic[cpu1]/thread=fffffe0003c0bc20: BAD TRAP: type=e" > "$T/il-panic.log"
printf '%s\n' "Fatal trap 12: page fault while in kernel mode" "panic: page fault" > "$T/fb-panic.log"
chk "log_panicked: NetBSD's heartbeat panic, after its timestamp" log_panicked "$T/nb-panic.log"
chk "log_panicked: illumos' panic[cpuN]/thread=" log_panicked "$T/il-panic.log"
chk "log_panicked: FreeBSD's panic: at a line's start" log_panicked "$T/fb-panic.log"
chk "log_panicked: not ddb.onpanic, savecore's or a poweroff" eval '! log_panicked "$T/nb-fine.log"'
chk "log_panicked: not a console that isn't there" eval '! log_panicked "$T/none.log"'
printf '%s\r\n' "PANIC: vm_page_fault: unhandled page fault in kernel space" "Welcome to Kernel Debugging Land..." "Thread 1 \"main thread\" running on CPU 0" > "$T/hk-panic.log"
printf '%s\n' "KERN: Welcome to Haiku" "kdl: not the debugger's banner" > "$T/hk-fine.log"
chk "log_panicked: Haiku's kernel debugger" log_panicked "$T/hk-panic.log"
chk "log_panicked: not a Haiku boot that didn't enter it" eval '! log_panicked "$T/hk-fine.log"'
chk "wait_why: 2 is a panic" eval '[[ $(wait_why 2) == *"kernel panicked"* ]]'
chk "wait_why: 1 is no SSH" eval '[[ $(wait_why 1) == "no SSH" ]]'

R=$T/run BOOTS=0 UP_AT=1 PANICS=0 HT_NOTE=""
mkdir -p "$R"
sleep() { :; }
# shellcheck disable=SC2329,SC2317  # called by the functions eval'd above
vm_up() { BOOTS=$((BOOTS + 1)); echo "$BOOTS $CONSOLE_KEY $*" >> "$R/ups"
    if ((BOOTS <= PANICS)); then cp "$T/nb-panic.log" "$2"; else cp "$T/nb-fine.log" "$2"; fi; }
# shellcheck disable=SC2329,SC2317
sshk() { ((BOOTS >= UP_AT)); }
# shellcheck disable=SC2329,SC2317
ht_qemus() { :; }
# shellcheck disable=SC2329,SC2317
ht_note() { HT_NOTE+="${HT_NOTE:+; }$*"; }
CONSOLE_KEY=k1 resc_up key "$R/img" "$R/c.log" --memory 3072 > "$T/resc1.out" 2>&1; rc=$?
chk "resc_up: one boot where sshd answers" eval '((rc == 0 && BOOTS == 1)) && [[ ! -e $R/c.log.panic-1 && -z $HT_NOTE ]]'
chk "resc_up: vm_up gets the image, the console, the options and CONSOLE_KEY" hasx "$R/ups" "1 k1 $R/img $R/c.log --memory 3072"
rm -f "$R/ups"; BOOTS=0 UP_AT=2 PANICS=1 HT_NOTE=""
CONSOLE_KEY=k2 resc_up key "$R/img" "$R/c.log" --memory 3072 > "$T/resc2.out" 2>&1; rc=$?
chk "resc_up: a panic at boot boots it again, and that one answers" eval '((rc == 0 && BOOTS == 2))'
chk "resc_up: the same vm_up both times" eval '[[ $(cut -d" " -f2- "$R/ups" | sort -u | grep -c .) == 1 ]]'
chk "resc_up: the panicked console kept as LOG.panic-1" log_panicked "$R/c.log.panic-1"
chk "resc_up: the second boot's console at LOG" eval '! log_panicked "$R/c.log"'
chk "resc_up: a note with the panic's line" eval '[[ $HT_NOTE == *"panicked at boot"*"time has not advanced in 1501 heartbeats"* ]]'
rm -f "$R/ups" "$R/c.log.panic-1"; BOOTS=0 UP_AT=9 PANICS=9 HT_NOTE=""
CONSOLE_KEY= resc_up key "$R/img" "$R/c.log" > "$T/resc3.out" 2>&1; rc=$?
chk "resc_up: a panic on the second boot too is a failure (2), with no third" eval '((rc == 2 && BOOTS == 2))'
chk "resc_up: and says so" has "$T/resc3.out" "panicked at boot again"

# A NetBSD rescuer boots with one vCPU (heartbeat(9), resc_up): --cpus 1
# last, after the rescuer guest's own options, so vm-run's last --cpus is 1;
# no other platform's rescuer gets it, and the source's and the restored
# disk's boots, which aren't resc_up's, keep their CPUs.
# shellcheck disable=SC2034
PLAT=netbsd
rm -f "$R/ups" "$R/c.log.panic-1"; BOOTS=0 UP_AT=1 PANICS=0 HT_NOTE=""
CONSOLE_KEY=k1 resc_up key "$R/img" "$R/c.log" --memory 3072 --disk-serials bkoresc,a > "$T/resc4.out" 2>&1; rc=$?
chk "resc_up: a NetBSD rescuer boots with --cpus 1, last" eval '((rc == 0)) && hasx "$R/ups" "1 k1 $R/img $R/c.log --memory 3072 --disk-serials bkoresc,a --cpus 1"'
rm -f "$R/ups"; BOOTS=0 UP_AT=1 PANICS=0 HT_NOTE=""
CONSOLE_KEY= resc_up key "$R/img" "$R/c.log" --cpus 4 --memory 3072 > /dev/null 2>&1
chk "resc_up: a NetBSD rescuer guest's own --cpus is overridden by the last --cpus 1" eval '[[ $(tr " " "\n" < "$R/ups" | grep -A1 -x -- --cpus | tail -n 1) == 1 ]]'
rm -f "$R/ups"; BOOTS=0 UP_AT=2 PANICS=1 HT_NOTE=""
CONSOLE_KEY=k2 resc_up key "$R/img" "$R/c.log" --memory 3072 > /dev/null 2>&1; rc=$?
chk "resc_up: a NetBSD rescuer's second boot after a panic: --cpus 1 again, once" eval '((rc == 0 && BOOTS == 2)) && [[ $(grep -c -- " --memory 3072 --cpus 1$" "$R/ups") == 2 && $(grep -o -- --cpus "$R/ups" | grep -c .) == 2 ]]'
rm -f "$R/ups" "$R/c.log.panic-1"; BOOTS=0 UP_AT=1 PANICS=0 HT_NOTE=""
# shellcheck disable=SC2034
PLAT=freebsd
CONSOLE_KEY=k1 resc_up key "$R/img" "$R/c.log" --memory 3072 > /dev/null 2>&1
chk "resc_up: a FreeBSD rescuer gets no --cpus" eval 'hasx "$R/ups" "1 k1 $R/img $R/c.log --memory 3072" && no "$R/ups" "[-]-cpus"'
rm -f "$R/ups"; BOOTS=0
# The script's boots as it makes them: the source's and the restored disk's
# by vm_up itself, with no --cpus (so a NetBSD source keeps its own), and
# resc_up's line the one --cpus in the script's code.
grep -vE '^\s*#' "$BSD" > "$T/bsd-code"
grep -E 'logs/(source|restored)-serial\.log' "$T/bsd-code" | grep -E 'vm_up|resc_up' > "$T/guest-boots"
chk "the source's and the restored disk's boots: vm_up, not resc_up" eval '[[ $(grep -c . "$T/guest-boots") == 2 ]] && ! grep -q resc_up "$T/guest-boots"'
chk "the source's and the restored disk's boots: no --cpus" no "$T/guest-boots" "[-]-cpus"
# (Fixed strings, as the lines are written.)
one_cpus() { [[ $(grep -c -- --cpus "$T/bsd-code") == 1 ]] && grep -qxF '    [[ $PLAT == netbsd ]] && set -- "$@" --cpus 1' "$T/bsd-code"; }
chk "ht1-bsd.sh: resc_up's NetBSD line is its one --cpus" one_cpus
# vm_up hands vm-run its options just before the image (each of its three
# ways), and vm-run's last --cpus is QEMU's -smp.
sed -n '/^vm_up() {/,/^}$/p' "$BSD" | grep -F '"$T/vm-run" ' > "$T/vm-up-runs"
vm_up_last() { [[ $(grep -c . "$T/vm-up-runs") == 3 && $(grep -cF '"$@" "$image" <' "$T/vm-up-runs") == 3 ]]; }
chk "vm_up: vm-run gets resc_up's options last, before the image" vm_up_last
VMRUN=$HERE/../vm-run
smp_last() {
    grep -qxF '        --cpus) cpus=$2; shift 2 ;;' "$VMRUN" &&
        grep -qF -- '-enable-kvm -cpu host -smp "$cpus" ' "$VMRUN" &&
        [[ $(grep -cE '(^|[^[:alnum:]_])cpus=' "$VMRUN") == 2 ]]
}
chk "vm-run: --cpus sets cpus (the last one wins), the one other is its 2, and -smp is cpus" smp_last

# vm_wait with the console (the source's, the restored disk's, Haiku's
# restart): a panic on it ends the wait at once (2), and without one, or
# without LOG, it waits its whole 120 tries (1).
BOOTS=0 UP_AT=1
vm_wait key "$T/nb-panic.log"; rc=$?
chk "vm_wait: a panicked console is 2" eval '((rc == 2))'
NPOLL=0
# shellcheck disable=SC2329,SC2317
sshk() { NPOLL=$((NPOLL + 1)); ((BOOTS >= UP_AT)); }
vm_wait key "$T/nb-fine.log"; rc=$?
chk "vm_wait: a console with no panic waits it out (1)" eval '((rc == 1 && NPOLL == 120))'
NPOLL=0; vm_wait key; rc=$?
chk "vm_wait: no LOG waits it out (1)" eval '((rc == 1 && NPOLL == 120))'
BOOTS=1; vm_wait key "$T/nb-panic.log"; rc=$?
chk "vm_wait: SSH answering is 0, whatever the console" eval '((rc == 0))'

# backup_failed: the rebuild and the verify after a backup with no
# repository are a note, not FAILs of their own.
# shellcheck disable=SC2329,SC2317
say() { echo "$*" >> "$R/report.txt"; }
: > "$R/report.txt"
BACKUP_STATE=failed
chk "backup_failed: after a failed backup, 0" backup_failed rebuild
chk "backup_failed: and a note line, no FAIL" eval '[[ $(cat "$R/report.txt") == "note: rebuild not run: the backup failed (its FAIL lines above)" ]]'
: > "$R/report.txt"
BACKUP_STATE=ok
chk "backup_failed: after a backup that made the repository, 1" eval '! backup_failed rebuild'
BACKUP_STATE=""
chk "backup_failed: with no backup phase in this run, 1" eval '! backup_failed verify'
chk "backup_failed: and says nothing then" eval '[[ ! -s $R/report.txt ]]'

# put: ssh (stubbed, as timeout) with sshk's ConnectTimeout, once more on
# ssh's own 255 only.
PORT=2690 GUSER=root SSH_CTO=30 SSH_RCS=() NSSH=0
# shellcheck disable=SC2329,SC2317
timeout() { shift; "$@"; }
# shellcheck disable=SC2329,SC2317
ssh() { NSSH=$((NSSH + 1)); echo "$*" > "$R/ssh-args"; cat > /dev/null; return "${SSH_RCS[NSSH - 1]:-0}"; }
echo x > "$T/f"
SSH_RCS=(255 0) NSSH=0; put key "$T/f" /root/f 2>"$T/put.err"; rc=$?
chk "put: a 255 is tried once more, and that one counts" eval '((rc == 0 && NSSH == 2))'
chk "put: and says so" has "$T/put.err" "ssh failed \(255\), once more"
chk "put: ConnectTimeout=SSH_CTO" has "$R/ssh-args" " ConnectTimeout=30 "
SSH_RCS=(1) NSSH=0; put key "$T/f" /root/f 2>/dev/null; rc=$?
chk "put: the remote's own failure isn't tried again" eval '((rc == 1 && NSSH == 1))'
SSH_RCS=(255 255 0) NSSH=0; put key "$T/f" /root/f 2>/dev/null; rc=$?
chk "put: two 255s are a 255, with no third" eval '((rc == 255 && NSSH == 2))'

# list_tree_watched: the illumos listing under a watchdog
# (t2h-20261127T1312-9ca9: omnios58-mirror's verify listing hung, the
# rescuer idle, until the job's 30-minute stall).  sshk stubbed as the
# rescuer: the start, the polls (POLLS, one a poll, the last one again
# when they run out; "" an ssh that got no answer), the diag, the fetch.
# (The polls run in a command substitution: their count goes in a file.)
# shellcheck disable=SC2034
PLAT=illumos
POLLS=() NPOLLS=0 STARTED=0 DIAGS=0
# shellcheck disable=SC2329,SC2317
sshk() { local c=$2
    case $c in
        *"nohup /bin/sh /tmp/ht1-list.sh"*) STARTED=$((STARTED + 1)); echo "$c" > "$T/start.sh"; echo started ;;
        "/bin/sh /tmp/ht1-list.poll "*) echo x >> "$T/npolls"; local i; i=$(($(wc -l < "$T/npolls") - 1)); ((i < ${#POLLS[@]})) || i=$((${#POLLS[@]} - 1)); [[ -n ${POLLS[i]} ]] || return 255; echo "${POLLS[i]}" ;;
        "/bin/sh /tmp/ht1-list.diag") DIAGS=$((DIAGS + 1)); echo "== mdb -k: 0t4242::pid2proc"; echo "zio_wait+0x5d"; echo "${LOSTKICK:-}" ;;
        "cat /tmp/ht1-list.err"*) echo "f 644 0 0 1 1.0  ./a" ;;
        *) return 1 ;;
    esac
}
export HT_LIST_POLL_SEC=0
lw() { local r; : > "$T/npolls"; list_tree_watched key /mnt/dst "$T/lw.out" "$T/lw.err" "$T/lw-stall.txt"; r=$?; NPOLLS=$(wc -l < "$T/npolls"); return $r; }
rm -f "$T/lw-stall.txt"
POLLS=("run cpu=0:00,0:00, nread=10" "run cpu=0:01,0:00, nread=20" "done 0") NPOLLS=0 HT_LIST_STALL_SEC=5 HT_LIST_MAX_SEC=60; lw; rc=$?
chk "list_tree_watched: a listing that finishes is its exit status (0)" eval '((rc == 0 && NPOLLS == 3))'
chk "list_tree_watched: and its output read back" hasx "$T/lw.out" "f 644 0 0 1 1.0  ./a"
chk "list_tree_watched: and no stall report" eval '[[ ! -e $T/lw-stall.txt ]]'
chk "list_tree_watched: the rescuer runs list_tree's listing of DIR by itself" eval 'grep -qF "( cd /mnt/dst && { find . " "$T/start.sh" && grep -qF "> /tmp/ht1-list.out 2> /tmp/ht1-list.err" "$T/start.sh"'
chk "list_tree_watched: its exit status kept as the rescuer's \$?, after this start's token" eval 'grep -qE "^echo \"[0-9]+\.1\.[0-9]+ \\\$\?\" > /tmp/ht1-list.rc.t; mv /tmp/ht1-list.rc.t /tmp/ht1-list.rc\$" "$T/start.sh"'
POLLS=("done 2") NPOLLS=0; lw; rc=$?
chk "list_tree_watched: a listing that failed is its exit status (2)" eval '((rc == 2))'
# shellcheck disable=SC2034  # (read by the eval'd checks and list_tree_watched)
t0=$SECONDS
# shellcheck disable=SC2034  # (read by the eval'd checks and list_tree_watched)
POLLS=("run cpu=0:00,0:00, nread=10" "run cpu=0:01,0:00, nread=12") NPOLLS=0 DIAGS=0 HT_LIST_STALL_SEC=2 HT_LIST_MAX_SEC=60 LIST_WHY=""; lw; rc=$?
chk "list_tree_watched: a hung listing is 124 after HT_LIST_STALL_SEC, not the hour's limit" eval '((rc == 124 && SECONDS - t0 < 10))'
chk "list_tree_watched: LIST_WHY names the stall and where its state is" eval '[[ $LIST_WHY == "no progress for "[0-9]*" s (run cpu=0:01,0:00, nread=12), the rescuer'"'"'s state in lw-stall.txt" ]]'
chk "list_tree_watched: the rescuer's state (stacks) in DIAG, once" eval '((DIAGS == 1)) && has "$T/lw-stall.txt" "^ht1-bsd: the listing of /mnt/dst: no progress for " && hasx "$T/lw-stall.txt" "zio_wait+0x5d"'
LOSTKICK="HT1-LOSTKICK vioblk#1 bkodisk-0: avail idx 2fea, used idx 2fe9 until a read of the disk notified it again (used idx 2ff9 after)"
POLLS=("run cpu=0:00, nread=10") HT_LIST_STALL_SEC=2; lw; rc=$?
chk "list_tree_watched: a lost virtio notify the diag found is in LIST_WHY" eval '((rc == 124)) && [[ $LIST_WHY == *", the rescuer'"'"'s state in lw-stall.txt; the rescuer'"'"'s virtio queue lost a notify (illumos): vioblk#1 bkodisk-0: avail idx 2fea, used idx 2fe9 until a read of the disk notified it again (used idx 2ff9 after)" ]]'
LOSTKICK=""
POLLS=("") NPOLLS=0 HT_LIST_STALL_SEC=2; lw; rc=$?
chk "list_tree_watched: a rescuer that never answers is 124 too, and says so" eval '((rc == 124)) && [[ $LIST_WHY == *"(no answer from the rescuer)"* ]]'
n=0; POLLS=(); for n in $(seq 1 400); do POLLS+=("run cpu=0:0$n, nread=$n"); done
# shellcheck disable=SC2034  # (read by the eval'd checks and list_tree_watched)
NPOLLS=0 HT_LIST_STALL_SEC=30 HT_LIST_MAX_SEC=2; lw; rc=$?
chk "list_tree_watched: one that keeps going past HT_LIST_MAX_SEC is 125" eval '((rc == 125)) && [[ $LIST_WHY == "still running after "* ]]'
# shellcheck disable=SC2329,SC2317
sshk() { return 255; }
lw; rc=$?
chk "list_tree_watched: one that can't be started is 255, and says so" eval '((rc == 255)) && [[ $LIST_WHY == "it could not be started in the rescuer" ]]'
chk "list_poll_sh: done with the status of its own token only" eval 'list_poll_sh > "$T/poll.sh"; d=$(mktemp -d); sed "s|/tmp/ht1-list|$d/l|g" "$T/poll.sh" > "$d/p.sh"; echo "tok1 3" > "$d/l.rc"; [[ $(sh "$d/p.sh" tok1) == "done 3" && $(sh "$d/p.sh" tok2) == "run "* ]]'
chk "list_poll_sh: else the listing's CPU and the pools' reads but rpool's" eval 'list_poll_sh | grep -qF "kstat -p -m zfs -c disk -s nread" && list_poll_sh | grep -qF ":rpool:nread"'
chk "list_diag_sh: kernel stacks (mdb, no pstack or pfiles: they hang on a process in zio_wait) before the pool commands" eval 'list_diag_sh > "$T/diag.sh" && before "$T/diag.sh" "::findstack -v" "zpool status" && before "$T/diag.sh" "::stacks -m zfs" "zpool status" && no "$T/diag.sh" "^ *(pstack|pfiles) "'
chk "list_diag_sh: the queues read before the disks are, and after" eval 'before "$T/diag.sh" "vq > /tmp/ht1-list.vq0" "dd if=" && before "$T/diag.sh" "dd if=" "vq > /tmp/ht1-list.vq1"'
# Its verdict, on the queues t2h-20261127T1312-9ca9's repro found.
grep "^awk 'NR == FNR" "$T/diag.sh" | sed 's|/tmp/ht1-list.vq0 /tmp/ht1-list.vq1|"$1" "$2"|' > "$T/verdict.sh"
printf '%s\n' "vioblk#0 bkoresc: in flight 0, avail idx 1edf, used flags/idx 0 1edf" "vioblk#1 bkodisk-0: in flight 1, avail idx 2fea, used flags/idx 0 2fe9" "vioblk#2 bkodisk-1: in flight 0, avail idx 6c8a, used flags/idx 0 6c8a" > "$T/vq0"
printf '%s\n' "vioblk#0 bkoresc: in flight 0, avail idx 1ee0, used flags/idx 0 1ee0" "vioblk#1 bkodisk-0: in flight 3, avail idx 2ffc, used flags/idx 0 2ff9" "vioblk#2 bkodisk-1: in flight 0, avail idx 6c8b, used flags/idx 0 6c8b" > "$T/vq1"
chk "list_diag_sh: a queue a read released is HT1-LOSTKICK, the others aren't" eval '[[ $(sh "$T/verdict.sh" "$T/vq0" "$T/vq1") == "HT1-LOSTKICK vioblk#1 bkodisk-0: avail idx 2fea, used idx 2fe9 until a read of the disk notified it again (used idx 2ff9 after)" ]]'
printf '%s\n' "vioblk#1 bkodisk-0: in flight 1, avail idx 2fea, used flags/idx 0 2fe9" > "$T/vq1b"
chk "list_diag_sh: one the read didn't release isn't" eval '[[ -z $(sh "$T/verdict.sh" "$T/vq0" "$T/vq1b") ]]'
printf '%s\n' "vioblk#1 bkodisk-0: in flight 1, avail idx 2fea, used flags/idx 1 2fe9" > "$T/vq0c"
chk "list_diag_sh: nor one whose device asked for no notify (used flags 1)" eval '[[ -z $(sh "$T/verdict.sh" "$T/vq0c" "$T/vq1") ]]'
chk "list_diag_sh, list_poll_sh: sh that parses" eval 'list_diag_sh | sh -n && list_poll_sh | sh -n'
# The illumos listings of the backup and the verify go through it.
chk "ht1-bsd.sh: the backup's illumos listing under the watchdog" grep -qF 'list_tree_watched "$rkey" /mnt/src "$R/src.listing" "$R/logs/fsmeta-src.log" "$R/logs/backup-list-stall.txt"' "$BSD"
chk "ht1-bsd.sh: the verify's illumos listing under the watchdog" grep -qF 'list_tree_watched "$rkey" /mnt/dst "$R/dst.listing" "$R/logs/fsmeta-dst.log" "$R/logs/verify-list-stall.txt"' "$BSD"
chk "ht1-bsd.sh: a stalled verify listing is a FAIL that names it" grep -qF 'bad "mtree: not compared: the listing of the restored files in the rescuer: $LIST_WHY"' "$BSD"
unset HT_LIST_POLL_SEC HT_LIST_STALL_SEC HT_LIST_MAX_SEC

if ((nfail == 0)); then echo "ht1-bsd-test: all $ncase cases ok"; exit 0; fi
echo "ht1-bsd-test: $nfail of $ncase cases FAIL"
exit 1
