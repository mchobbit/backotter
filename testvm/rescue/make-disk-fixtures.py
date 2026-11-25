#!/usr/bin/env python3
"""The fake bko-holt's scenarios for RT12's Disks, Passphrases, Resume and
Review screens (rescue-tui.md, RT12), written into scenarios/:

    make-disk-fixtures.py

  app-rec-debian13   recorded: debian13-stack's whole-run plan and its status
                     after the partition stage, as HoltTest1 HT_VARIANT=json
                     recorded them (scenarios/rec-debian13-stack, 04-plan and
                     08-status-after-partition), answering every plan and
                     status call bko-rescue makes; the stick is stick-three's
  app-lovelace       hand-written: rescue-screens.md's sample machine, lovelace
                     (a Samsung 500 GB and a WD Red 2 TB, LUKS on both), on a
                     computer with a Crucial P3 1 TB, the WD Red moved over, a
                     Kingston 480 GB with photos and the stick: the Disks
                     mock's, the offer's (the Kingston, 4.3 % smaller) and the
                     much bigger disk's (the Crucial)
  app-lovelace-dry   app-lovelace, its api document app-dry's (no_commit true)
  app-lovelace-small hand-written: the "Too small for the data" variant, a WDC
                     320 GB (38.7 % smaller: the user decides) and a Transcend
                     64 GB (the data doesn't fit)

The hand-written documents are what bko-holt would write for those disks,
by fit's own rules (a margin of max(1 GiB, 5 % of the space used), the most
being the disk less the other partitions): no guest has these disks, so
none is recorded.  Every one passes holt_schema.py, as bko-holt's own do.

Each call's argv is the shape bko-rescue builds (src/app/holt_args.rs):

    load LAYOUT --json STAGE --repo R --password-file F [--key-file N=F]...
         [--map S=T]... [--erase T]... --snapshot ID [--fit-largest P]
         [--reformat M=T]...

so the Rust tests' fake and fake-bko-holt answer the same calls.  Each
scenario also has `system.json`, what the tests' read-only mount of the
config partition finds as the machine's settings.

Python's stdlib only.
"""

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SC = os.path.join(HERE, "scenarios")
GEN = "2026-11-25T18:00:00Z"
GiB = 1 << 30


def sha(n):
    return ("%064x" % (0x51d0 + n))


def load(stage, keys=(), maps=(), erase=0, fit=None, reformat=()):
    a = ["load", "*", "--json"]
    a += {"plan": ["--through", "plan"], "status": ["--only", "status"]}[stage]
    a += ["--repo", "*", "--password-file", "*"]
    for k in keys:
        a += ["--key-file", "%s=*" % k]
    for s, t in maps:
        a += ["--map", "%s=%s" % (s, t)]
    a += ["--erase", "*"] * erase
    a += ["--snapshot", "*"]
    if fit is not None:
        a += ["--fit-largest", str(fit)]
    for m in reformat:
        a += ["--reformat", m]
    return a


def write(scen, name, argv, doc=None, exit=None, raw=None):
    d = os.path.join(SC, scen, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "argv"), "w") as f:
        f.write("\n".join(argv) + "\n")
    with open(os.path.join(d, "stdout"), "w") as f:
        f.write(raw if raw is not None else json.dumps(doc) + "\n")
    if exit is not None:
        with open(os.path.join(d, "exit"), "w") as f:
            f.write("%d\n" % exit)


def fresh(scen):
    d = os.path.join(SC, scen)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)


def copy_call(src_scen, src_call, scen, name):
    shutil.copytree(os.path.join(SC, src_scen, src_call), os.path.join(SC, scen, name))


SYSTEM_JSON = {"format": "backotter-system", "schema": 1, "machine": {"install_id": "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"},
               "note": "a test's: what the read-only mount of the config partition finds"}


LOVELACE_PATH = "backotter/machines/0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90/system.json"


def stick(scen, version="stick-three"):
    copy_call(version, "01-version", scen, "01-version")
    copy_call("stick-three", "02-stick", scen, "02-stick")
    with open(os.path.join(SC, scen, "system.json"), "w") as f:
        json.dump(SYSTEM_JSON, f)
        f.write("\n")
    # Where fake-bin/mount puts it: lovelace's path on the stick.
    with open(os.path.join(SC, scen, "system.path"), "w") as f:
        f.write(LOVELACE_PATH + "\n")


# ---- app-rec-debian13: recorded documents ---------------------------------

def rec():
    scen = "app-rec-debian13"
    fresh(scen)
    stick(scen)
    plan = open(os.path.join(SC, "rec-debian13-stack", "04-plan", "stdout")).read()
    status = open(os.path.join(SC, "rec-debian13-stack", "08-status-after-partition", "stdout")).read()
    maps = [("vda", "*"), ("vdb", "*")]
    n = 10
    for stage, keys, m, erase in [
        ("plan", (), (), 0),
        ("plan", (), maps, 2),
        ("plan", ("md1_crypt",), maps, 2),
        ("plan", ("md1_crypt",), maps, 0),
        ("status", (), maps, 0),
        ("status", ("md1_crypt",), maps, 0),
    ]:
        write(scen, "%02d-%s" % (n, stage), load(stage, keys, m, erase), raw=plan if stage == "plan" else status)
        n += 1
    with open(os.path.join(SC, scen, "README"), "w") as f:
        f.write("Recorded documents (rec-debian13-stack's 04-plan and 08-status-after-partition)\n"
                "answering every plan and status call of bko-rescue's Disks to Review; see\n"
                "../../make-disk-fixtures.py.\n")


# ---- lovelace: hand-written by fit's rules ---------------------------------

SAMSUNG = 500_107_862_016
WDRED = 2_000_398_934_016
OTHERS_A = 35_107_862_016          # sda1 (EFI), sda2 (/boot), sda4 (swap)
ROOT_OLD = SAMSUNG - OTHERS_A       # sda3, LUKS, ext4 /: 465 GB
ROOT_USED = 41_700_000_000
SRV_OLD = WDRED - 1_048_576
SRV_USED = 1_210_000_000_000


def margin(used):
    return max(GiB, used * 5 // 100)


def needed(used):
    # statvfs's size leaves out the metadata: a little above used + margin.
    return used + margin(used) + used // 400


def size(src, node_bytes):
    """sizes[] for old disk SRC on a disk of NODE_BYTES, as fit gives it."""
    old, used, other, mount, part, total = {
        "sda": (ROOT_OLD, ROOT_USED, OTHERS_A, "/", "sda3", SAMSUNG),
        "sdb": (SRV_OLD, SRV_USED, WDRED - SRV_OLD, "/srv", "sdb1", WDRED),
    }[src]
    v = {"source": src, "verdict": "same", "fit_pct": None, "change_pct": None, "partition": part, "mount": mount,
         "old_bytes": old, "new_bytes": None, "used_bytes": used, "margin_bytes": None, "needed_bytes": None,
         "most_bytes": None, "data_fits": None, "reason": None, "grow": None, "why": None}
    if node_bytes == total:
        return v
    if node_bytes > total:
        extra = node_bytes - total
        pct = -(-extra * 100 // old)
        v.update(verdict="bigger", data_fits=True)
        if pct <= 50:
            v.update(fit_pct=pct, new_bytes=old + extra, change_pct=round(extra * 1000 / old) / 10)
        return v
    most = (node_bytes - other) // (1 << 20) * (1 << 20)
    need = needed(used)
    v.update(margin_bytes=margin(used), needed_bytes=need, most_bytes=most, data_fits=need <= most)
    if need > most:
        v.update(verdict="data_wont_fit", reason="data",
                 why="%s holds %d bytes; the most it could get on this disk is %d bytes" % (mount, used, most))
        return v
    change = round((old - most) * 1000 / old) / 10
    pct = -(-(old - most) * 100 // old)
    v.update(verdict="smaller" if pct <= 50 else "beyond_limit", fit_pct=pct if pct <= 50 else None,
             new_bytes=most, change_pct=change)
    return v


def cand(node, by_id, size_bytes, model, serial, transport, contents=(), refused=None, removable=False):
    return {"node": node, "path": "/dev/" + node, "by_id": by_id, "size_bytes": size_bytes, "model": model,
            "serial": serial, "wwid": None, "sector": 512, "transport": transport, "removable": removable,
            "serial_shared": False, "target_of": None, "ours": False, "blank": not contents,
            "contents": list(contents), "refused": refused, "erase_needed": bool(contents),
            "sizes": [] if refused else [size("sda", size_bytes), size("sdb", size_bytes)]}


CT1000 = cand("nvme0n1", "/dev/disk/by-id/nvme-CT1000P3PSSD8_2318E6F3A9C1", 1_000_204_886_016, "CT1000P3PSSD8", "2318E6F3A9C1", "nvme")
WDRED_HERE = cand("sdb", "/dev/disk/by-id/ata-WDC_WD20EFRX-68EUZN0_WD-WCC4M5XK7HPL", WDRED, "WDC WD20EFRX-68EUZN0", "WD-WCC4M5XK7HPL", "sata",
                  contents=[{"dev": "sdb1", "type": "crypto_LUKS", "label": None, "size_bytes": SRV_OLD, "used_bytes": None}])
KINGSTON = cand("sda", "/dev/disk/by-id/ata-KINGSTON_SA400S37480G_50026B7782A1F3C4", 480_103_981_056, "KINGSTON SA400S37480G", "50026B7782A1F3C4", "sata",
                contents=[{"dev": "sda1", "type": "ext4", "label": "photos", "size_bytes": 480_102_000_000, "used_bytes": 312_000_000_000}])
STICK = cand("sdc", "/dev/disk/by-id/usb-SanDisk_Ultra_4C530001240526108341-0:0", 32_015_679_488, "SanDisk Ultra", "4C530001240526108341", "usb",
             contents=[{"dev": "sdc3", "type": "vfat", "label": "BACKOTTER", "size_bytes": 268_435_456, "used_bytes": None}],
             refused={"code": "boot_disk", "text": "sdc is the disk this system booted from; never a target"}, removable=True)
WD320 = cand("sda", "/dev/disk/by-id/ata-WDC_WD3200AAKX-00ERMA0_WD-WCAV2K318842", 320_072_933_376, "WDC WD3200AAKX", "WD-WCAV2K318842", "sata")
TS64 = cand("sdd", "/dev/disk/by-id/ata-TS64GSSD370S_F431270087", 64_023_257_088, "TS64GSSD370S", "F431270087", "sata")

SOURCES = [
    {"name": "sda", "size_bytes": SAMSUNG, "model": "Samsung SSD 870 EVO 500GB", "serial": "S6PXNM0T412345A", "wwid": None,
     "sector": 512, "table": "gpt", "mounts": ["/", "/boot", "/boot/efi"],
     "largest": {"partition": "sda3", "size_bytes": ROOT_OLD, "holds": "LUKS2, ext4", "mount": "/", "used_bytes": ROOT_USED}},
    {"name": "sdb", "size_bytes": WDRED, "model": "WDC WD20EFRX-68EUZN0", "serial": "WD-WCC4M5XK7HPL", "wwid": None,
     "sector": 512, "table": "gpt", "mounts": ["/srv"],
     "largest": {"partition": "sdb1", "size_bytes": SRV_OLD, "holds": "LUKS2, ext4", "mount": "/srv", "used_bytes": SRV_USED}},
]

REFORMAT = [
    {"mount": "/", "type": "ext4", "to": {"xfs": None, "btrfs": {"code": "reformat_refused", "reason": "no_fsck",
     "text": "--reformat /=btrfs: the source doesn't have btrfs-progs installed; install it there and back up again, or the restored system has no fsck or repair for btrfs"}}},
    {"mount": "/boot", "type": "ext4", "to": {
        "xfs": {"code": "reformat_refused", "reason": "grub_reads", "text": "--reformat /boot=xfs: GRUB reads /boot from it; under GRUB, --reformat makes ext4 only"},
        "btrfs": {"code": "reformat_refused", "reason": "grub_reads", "text": "--reformat /boot=btrfs: GRUB reads /boot from it; under GRUB, --reformat makes ext4 only"}}},
    {"mount": "/srv", "type": "ext4", "to": {"xfs": None, "btrfs": None}},
]

TEXT = """# bko-holt load plan for /run/bko-rescue/layout.json
# Source: lovelace, Debian GNU/Linux 13 (trixie), UEFI boot with grub, dumped 2026-11-22T01:00:11Z
#
# Disks:
#   sda (466 GiB) -> {sda}
#   sdb (1.8 TiB) -> {sdb}

# ---- partition ----
# sda -> {sda}: clear the old table and signatures
wipefs --all {sda}
sfdisk --wipe always --wipe-partitions always {sda} <<'EOF'
label: gpt
label-id: 7D2B4E10-3C9A-4F6B-9E21-5A8C0D4F3B77
first-lba: 2048
{sda}p1 : start=2048, size=1048576, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=0D6C2B6E-7F1A-4B44-9E8E-3C3A5A1D9B21
EOF
# ---- assemble ----
# cryptroot: LUKS2, the source's UUID and cipher, a new key
cryptsetup luksFormat --batch-mode --type=luks2 --uuid=6f1c2a9e-4b7d-4f3e-a2c1-9d8e7f6a5b4c --key-file /run/bko-rescue/k0 {sda}3
cryptsetup open --key-file /run/bko-rescue/k0 {sda}3 cryptroot
# ---- format ----
mke2fs -F -q -t ext4 -O none,has_journal,ext_attr,dir_index,filetype,extent,64bit,flex_bg,sparse_super,large_file,huge_file,dir_nlink,extra_isize,metadata_csum /dev/mapper/cryptroot
"""


def plan(cands, targets, maps=(), keys_given=False, fit=None, reformat=(), extra_problems=()):
    """A plan document: CANDS here, TARGETS {source: (cand, matched_by)}."""
    cands = json.loads(json.dumps(cands))
    disks, problems, notes, fits = [], [], [], []
    for src in SOURCES:
        t = targets.get(src["name"])
        target = None
        if t is not None:
            c, by = t
            c = next(x for x in cands if x["node"] == c["node"])
            c["target_of"] = src["name"]
            sz = size(src["name"], c["size_bytes"])
            target = dict({k: c[k] for k in ("node", "path", "by_id", "size_bytes", "model", "serial", "wwid", "sector", "transport", "removable")},
                          matched_by=by, same_identity=c["serial"] == src["serial"], blank=c["blank"], contents=c["contents"],
                          ours=False, recorded=False, erase=bool(maps), will_erase=True, writes=["partition", "assemble", "format"],
                          size=None if sz["verdict"] == "same" else sz)
            if sz["verdict"] != "same" and fit is None and sz["verdict"] != "bigger":
                problems.append({"code": "target_too_small", "text": "%s -> %s: the target is smaller than the source (%d < %d bytes); --fit-largest %s would fit it"
                                 % (src["name"], c["path"], c["size_bytes"], src["size_bytes"], sz["fit_pct"]),
                                 "disk": src["name"], "target": c["node"], "target_bytes": c["size_bytes"], "source_bytes": src["size_bytes"]})
            if fit is not None:
                old = src["largest"]["size_bytes"]
                if sz["verdict"] == "bigger":
                    new = old + min(c["size_bytes"] - src["size_bytes"], old * fit // 100)
                elif sz["verdict"] == "same":
                    new = old
                else:
                    new = sz["new_bytes"]
                fits.append({"disk": src["name"], "target": c["node"], "pct": fit, "sector": 512,
                             "old_bytes": src["size_bytes"], "new_bytes": c["size_bytes"],
                             "old_last_sector": src["size_bytes"] // 512 - 34, "new_last_sector": c["size_bytes"] // 512 - 34,
                             "partition": {"number": 3 if src["name"] == "sda" else 1, "name": src["largest"]["partition"], "what": "ext4 in LUKS2",
                                           "old_sectors": old // 512, "new_sectors": new // 512},
                             "moved": [], "data": [], "notes": []})
                if new != old:
                    notes.append({"code": "fit_change", "text": "%s -> %s: %s from %d to %d bytes (--fit-largest %d)" % (src["name"], c["path"], src["largest"]["partition"], old, new, fit),
                                  "disk": src["name"], "target": c["node"]})
        else:
            problems.append({"code": "not_mapped", "text": "%s: no target disk; give --map %s=DEV" % (src["name"], src["name"]), "disk": src["name"]})
        disks.append({"source": src, "target": target})
    if not keys_given:
        problems.append({"code": "key_needed", "text": "cryptroot: give its new passphrase with --key-file cryptroot=FILE", "name": "cryptroot", "group": "cryptroot"})
    problems.extend(extra_problems)
    for m in reformat:
        mount, to = m.split("=")
        notes.append({"code": "reformat", "text": "%s is made as %s" % (mount, to), "mount": mount, "from": "ext4", "to": to})
    tg = {s: (targets[s][0]["path"] if s in targets else "/dev/" + s) for s in ("sda", "sdb")}
    return {
        "format": "bko-holt-plan", "schema": 1, "version": "0.2.6", "generated": GEN, "dry_run": False,
        "ok": not problems, "exit": 4 if problems else 0, "plan_sha256": sha(len(maps) * 7 + (fit or 0) + 3 * len(reformat) + int(keys_given)),
        "layout": {"path": "/run/bko-rescue/s-1-0/layout.json", "sha256": sha(1)},
        "args": ["load", "/run/bko-rescue/s-1-0/layout.json", "--json"],
        "source": {"hostname": "lovelace", "os": {"id": "debian", "version_id": "13", "name": "Debian GNU/Linux 13 (trixie)"},
                   "family": "linux", "arch": "x86_64", "boot_mode": "uefi", "loader": "grub",
                   "generated": "2026-11-22T01:00:11Z", "dmi_uuid": "4c4c4544-0042-4b10-8031-b3c04f4e3332", "reformat_possible": True},
        "here": {"dmi_uuid": "5a6b7c8d-1e2f-4a3b-8c4d-5e6f7a8b9c0d", "boot_mode": "uefi", "efivars": True, "arch": "x86_64",
                 "privileged": True, "same_machine": False},
        "disks": disks, "candidates": cands,
        "erase": [c[0]["by_id"] for c in targets.values()] if maps else [],
        "keys": [{"group": "cryptroot", "kind": "luks", "names": ["cryptroot", "cryptsrv"], "devices": ["sda3", "sdb1"],
                  "holds": ["/", "/srv"], "unlock": "passphrase", "given": keys_given, "new_ok": True,
                  "min_bytes": 1, "max_bytes": 512, "shared_at_boot": True}],
        "tokens": [], "reformat": REFORMAT, "fit": fits, "problems": problems, "notes": notes,
        "stages": [{"name": "partition", "commands": [{"kind": "run", "argv": ["wipefs", "--all", tg["sda"]], "env": [["LC_ALL", "C"]],
                                                       "why": "sda -> %s: clear the old table and signatures" % tg["sda"], "path": None, "dev": None,
                                                       "stdin": None, "stdin_bytes": None, "stdin_sha256": None, "private": False, "archiver": False}]}],
        "text": TEXT.format(sda=tg["sda"], sdb=tg["sdb"]),
    }


STATUS_FRESH = {
    "format": "bko-holt-status", "schema": 1, "version": "0.2.6", "generated": GEN, "dry_run": False, "exit": 0,
    "target": "/mnt/bko-target",
    "stages": [{"name": n, "state": "not_done" if n in ("partition", "close") else "unknown", "why": ""} for n in
               ("partition", "assemble", "format", "mount", "restore", "boot", "close")],
    "next": "partition", "all_done": False,
    "marks": {"partitioned": [], "restore_started": False, "restore_complete": False, "boot": False},
    "targets": [{"source": "sda", "node": "nvme0n1", "ours": False, "same_identity": False, "recorded": False},
                {"source": "sdb", "node": "sdb", "ours": True, "same_identity": True, "recorded": False}],
    "temporary_keys": [], "resume": {"go_on": False, "why": ["nothing of this layout's is on these disks"]},
    "open": {"containers": [], "pools": [], "mounted": []}, "problems": [],
}

CT = "*nvme-CT1000P3PSSD8*"
WD = "*WDC_WD20EFRX*"
KG = "*KINGSTON*"
W3 = "*WD3200AAKX*"


def lovelace(scen="app-lovelace", version="stick-three"):
    fresh(scen)
    stick(scen, version)
    cands = [CT1000, WDRED_HERE, KINGSTON, STICK]
    n = [10]

    def w(argv, doc):
        write(scen, "%02d" % n[0], argv, doc)
        n[0] += 1
    w(load("plan"), plan(cands, {"sdb": (WDRED_HERE, "serial")}))
    # The Crucial for old disk 1, then both named on OK.
    w(load("plan", maps=[("sda", CT)], erase=1), plan(cands, {"sda": (CT1000, "map"), "sdb": (WDRED_HERE, "serial")}, maps=[1]))
    w(load("plan", maps=[("sda", CT), ("sdb", WD)], erase=2), plan(cands, {"sda": (CT1000, "map"), "sdb": (WDRED_HERE, "map")}, maps=[1, 2]))
    keys = ("cryptroot", "cryptsrv")
    both = {"sda": (CT1000, "map"), "sdb": (WDRED_HERE, "map")}
    w(load("plan", keys, [("sda", CT), ("sdb", WD)], 2), plan(cands, both, maps=[1, 2], keys_given=True))
    w(load("plan", keys, [("sda", CT), ("sdb", WD)], 2, reformat=["/srv=xfs"]), plan(cands, both, maps=[1, 2], keys_given=True, reformat=["/srv=xfs"]))
    # The Kingston: the offer's second plan, then on.
    kg = {"sda": (KINGSTON, "map"), "sdb": (WDRED_HERE, "serial")}
    w(load("plan", maps=[("sda", KG)], erase=1, fit=5), plan(cands, kg, maps=[1], fit=5))
    kg2 = {"sda": (KINGSTON, "map"), "sdb": (WDRED_HERE, "map")}
    w(load("plan", maps=[("sda", KG), ("sdb", WD)], erase=2, fit=5), plan(cands, kg2, maps=[1, 2], fit=5))
    w(load("plan", keys, [("sda", KG), ("sdb", WD)], 2, fit=5), plan(cands, kg2, maps=[1, 2], keys_given=True, fit=5))
    for k in ((), keys):
        w(load("status", k, [("sda", CT), ("sdb", WD)]), STATUS_FRESH)
        w(load("status", k, [("sda", KG), ("sdb", WD)], fit=5), STATUS_FRESH)
    with open(os.path.join(SC, scen, "README"), "w") as f:
        f.write("Hand-written by ../../make-disk-fixtures.py: rescue-screens.md's lovelace.\n")


def lovelace_small():
    scen = "app-lovelace-small"
    fresh(scen)
    stick(scen)
    cands = [WD320, WDRED_HERE, TS64, STICK]
    n = [10]

    def w(argv, doc):
        write(scen, "%02d" % n[0], argv, doc)
        n[0] += 1
    w(load("plan"), plan(cands, {"sdb": (WDRED_HERE, "serial")}))
    pct = size("sda", WD320["size_bytes"])["fit_pct"]
    w(load("plan", maps=[("sda", W3)], erase=1, fit=pct), plan(cands, {"sda": (WD320, "map"), "sdb": (WDRED_HERE, "serial")}, maps=[1], fit=pct))
    w(load("plan", maps=[("sda", W3), ("sdb", WD)], erase=2, fit=pct), plan(cands, {"sda": (WD320, "map"), "sdb": (WDRED_HERE, "map")}, maps=[1, 2], fit=pct))
    w(load("plan", ("cryptroot", "cryptsrv"), [("sda", W3), ("sdb", WD)], 2, fit=pct),
      plan(cands, {"sda": (WD320, "map"), "sdb": (WDRED_HERE, "map")}, maps=[1, 2], keys_given=True, fit=pct))
    for k in ((), ("cryptroot", "cryptsrv")):
        w(load("status", k, [("sda", W3), ("sdb", WD)], fit=pct), STATUS_FRESH)
    with open(os.path.join(SC, scen, "README"), "w") as f:
        f.write("Hand-written by ../../make-disk-fixtures.py: the \"Too small for the data\" variant.\n")


if __name__ == "__main__":
    rec()
    lovelace()
    lovelace("app-lovelace-dry", "app-dry")
    lovelace_small()
