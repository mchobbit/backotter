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
want=(geli_provs sr_vols geli_fstab_mp onetime_swaps geli_files_list geli_slot1_script mount_script umount_script)
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

if ((nfail == 0)); then echo "ht1-bsd-test: all $ncase cases ok"; exit 0; fi
echo "ht1-bsd-test: $nfail of $ncase cases FAIL"
exit 1
