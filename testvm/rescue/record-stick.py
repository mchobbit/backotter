#!/usr/bin/env python3
"""Records the fake's `stick-*` scenarios (RT11's fixtures) from a real
bko-holt: `bko-holt --version --json`, `bko-holt stick --json` (RT10k),
`bko-holt recovery-words OUT --json` (RT10p) and `bko-holt stick --copy
INSTALL_ID OUT --json` (holt-json.md's open question 8), each run as
bko-rescue runs it, its standard output and exit kept as a fake-bko-holt
call.

    record-stick.py BKO_HOLT OUT_DIR [SCENARIO...]

BKO_HOLT is a bko-holt built for this host (Linux); OUT_DIR gets one
scenario directory per stick (scenarios/ beside this file is where they
live), or only the SCENARIOs named.  Needs python3 3.11 or later, mkfs.vfat and mtools: each stick's
config partition is made by stick/marker.py (as make-img.sh makes the Linux
image's, 256 MiB, partition 3), its machines put on it with mmd and mcopy,
and bko-holt reads it through a Linux tree booted from that stick (`stick
--json --sysroot TREE`: /proc/self/mountinfo with casper's /cdrom on sdc1,
sysfs for sdc's partitions, /dev/sdc holding the config partition where
sysfs says it starts, the marker in /cdrom/.disk, this computer's DMI UUID).
The scenarios' argv is bko-rescue's (`stick --json`), not the --sysroot
the recording used.  The recovery words are BIP39's test vector, typed as
a person would; the call with the good words writes the password to OUT.
A `stick --copy` call's argv is `stick --copy ID * --json`, and what it
wrote to OUT is the call's `out`: stick-layouts' lovelace carries a real
dump as its system.json's `layout` (holt/tests/corpus/debian13-stack's
src.json), so that copy is the layout bko-rescue's scenarios load.
stick-left-out's machines carry a layout, one left out for the stick's
room (`stick_full`), one too big for any stick (`system_too_big`) and none
(holt-json.md's open questions 16 and 18): each row's `layout` says which,
and `stick --copy` of each is its `no_layout`.

Each scenario's recorded.txt says what and when.  Python's stdlib only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(HERE, "..", "..", "stick", "marker.py")
# The layout stick-layouts' lovelace carries: a real dump of the corpus.
DUMP = os.path.join(HERE, "..", "..", "holt", "tests", "corpus", "debian13-stack", "src.json")
CONFIG_PART = 3
CONFIG_MIB = 256
START = 4096                    # sdc3's first sector, as sysfs gives it

LOVELACE = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"
BABBAGE = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5"
HOPPER = "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6"
LOVELACE_DMI = "4c4c4544-0042-4b10-8031-b3c04f4e3332"
# lovelace reinstalled: a new install ID, the same hardware.
LOVELACE_NEW = "5f6a7b8c-9d0e-4f1a-8b2c-3d4e5f6a7b8c"

WORDS = "legal winner thank year wave sausage worth useful legal winner thank yellow"
# What bko-rescue sends: the 12 fields' words, each completed as the
# screen completes it (a word not on the list as typed).  The fake also
# takes a word's first 4 letters or more for it, as bko-holt does.
TYPED = {
    "01-good": WORDS + "\n",
    "02-unknown": "legal winner thank year wave sausage worth useful ribbx winner thank yellow\n",
    "03-checksum": "winner legal thank year wave sausage worth useful legal winner thank yellow\n",
    "04-count": "legal winner thank year wave sausage worth useful legal winner thank\n",
    "05-short": "legal winner thank year wave sausage worth useful legal winner tha yellow\n",
}


def system(install_id, hostname, os_id, version_id, dmi, written, seq, url, extra=None):
    d = {
        "format": "backotter-system", "schema": 1, "sequence": seq, "written": written,
        "written_by": "backotter 0.1.0",
        "machine": {"install_id": install_id, "hostname": hostname,
                    "dmi": {"system_uuid": dmi, "vendor": "LENOVO", "product": "21CB"}},
        "os": {"id": os_id, "version_id": version_id, "kernel": "7.0.0-30-generic",
               "boot_mode": "uefi", "secure_boot": True},
        "storage": {}, "boot": {},
        "backup": {"engine": "restic", "repository": {"kind": url.split(":", 1)[0], "url": url},
                   "repository_id": hashlib.sha256(hostname.encode()).hexdigest()[:16],
                   "credential": {"kind": "prompt"}},
        "requirements": {"zfs_features": [], "kernel_modules": []},
    }
    d.update(extra or {})
    return json.dumps(d, indent=2) + "\n"


def three():
    """The Machine screen's mock-up: lovelace, babbage, hopper.  None
    carries a layout, so a bko-holt whose rows say so (open question 18)
    makes each row `none`, and bko-rescue then shows its no-layout notice
    for each.  The scenarios built from these were recorded before that:
    re-record them only knowing their tests' key presses and goldens
    change (scenarios/README.md)."""
    return [
        (LOVELACE, system(LOVELACE, "lovelace", "debian", "13", LOVELACE_DMI.upper(), "2026-11-22T01:00:11Z", 42,
                          "sftp://nas.local/backotter/lovelace",
                          {"password_hint": "the boat's name, then the year we moved"}), "ok"),
        (BABBAGE, system(BABBAGE, "babbage", "fedora", "45", "11111111-2222-3333-4444-555555555555",
                         "2026-11-21T08:00:00Z", 17, "rest:http://10.0.0.5:8000/babbage"), "ok"),
        (HOPPER, system(HOPPER, "hopper", "freebsd", "15.0", "", "2026-11-18T01:00:00Z", 5,
                        "sftp://nas.local/backotter/hopper"), "ok"),
    ]


def layouts():
    """lovelace carrying a layout (a real dump), babbage none (today's
    system.json), hopper one whose checksum is wrong."""
    with open(DUMP) as f:
        dump = json.load(f)
    lv = system(LOVELACE, "lovelace", "debian", "13", LOVELACE_DMI.upper(), "2026-11-22T01:00:11Z", 42,
                "sftp://nas.local/backotter/lovelace", {"layout": dump})
    hp = system(HOPPER, "hopper", "debian", "13", "", "2026-11-18T01:00:00Z", 5,
                "sftp://nas.local/backotter/hopper", {"layout": dump})
    return [(LOVELACE, lv, "ok"), three()[1], (HOPPER, hp, "bad")]


TURING = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"


def without_layout(body, code, size):
    """BODY (a system.json) as the backup tool writes it when the stick
    can't take the whole file (bko-holt's stick::without_layout): no
    `layout`, and `layout_left_out` saying CODE and the whole file's SIZE."""
    d = json.loads(body)
    d.pop("layout", None)
    d["layout_left_out"] = {"code": code, "bytes": size}
    return json.dumps(d, indent=2) + "\n"


def left_out():
    """lovelace carrying its layout; babbage's left out for the stick's
    room (20 MiB with it), hopper's too big for any stick (70 MiB with it),
    turing's none, saying no reason."""
    with open(DUMP) as f:
        dump = json.load(f)
    lv = system(LOVELACE, "lovelace", "debian", "13", LOVELACE_DMI.upper(), "2026-11-22T01:00:11Z", 42,
                "sftp://nas.local/backotter/lovelace", {"layout": dump})
    bb = without_layout(system(BABBAGE, "babbage", "fedora", "45", "11111111-2222-3333-4444-555555555555",
                               "2026-11-21T08:00:00Z", 17, "rest:http://10.0.0.5:8000/babbage", {"layout": dump}),
                        "stick_full", 20 << 20)
    hp = without_layout(system(HOPPER, "hopper", "freebsd", "15.0", "", "2026-11-18T01:00:00Z", 5,
                               "sftp://nas.local/backotter/hopper"), "system_too_big", 70 << 20)
    tu = system(TURING, "turing", "debian", "12", "", "2026-11-01T00:00:00Z", 3, "sftp://nas.local/backotter/turing")
    return [(LOVELACE, lv, "ok"), (BABBAGE, bb, "ok"), (HOPPER, hp, "ok"), (TURING, tu, "ok")]


# Each stick: (scenario, machines [(id, system.json, how its .sha256 is)],
# this computer's DMI UUID, what's done to the stick, recovery calls, why,
# stick --copy calls [(name, install ID)]).
STICKS = [
    ("stick-three", three(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, True,
     "three machines, none backed up from this computer (the Machine screen's mock-up)",
     [("copy", LOVELACE)]),
    ("stick-layouts", layouts(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, False,
     "lovelace's system.json carrying a real dump as its layout, babbage's none, hopper's a wrong checksum: "
     "stick --copy of each, and of a machine not there",
     [("copy", LOVELACE), ("copy-no-layout", BABBAGE), ("copy-checksum-bad", HOPPER), ("copy-no-machine", TURING)]),
    ("stick-left-out", left_out(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, False,
     "lovelace's system.json carrying its layout, babbage's left out for the stick's room (stick_full), "
     "hopper's too big for any stick (system_too_big), turing's none: each row's layout, and stick --copy of the three",
     [("copy-stick-full", BABBAGE), ("copy-too-big", HOPPER), ("copy-none", TURING)]),
    ("stick-this-computer", three(), LOVELACE_DMI, None, True,
     "the same three, lovelace backed up from this computer"),
    ("stick-one", three()[:1], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, False,
     "one machine, lovelace, on a new computer"),
    ("stick-failing", [three()[0], (BABBAGE, three()[1][1], "bad"),
                       (HOPPER, three()[2][1].replace('"schema": 1', '"schema": 2'), "ok"),
                       ("3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7", system("3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7", "turing", "debian", "12", "", "2026-11-01T00:00:00Z", 3, "sftp://nas.local/backotter/turing"), "none")],
     "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, False,
     "lovelace ok; babbage's checksum wrong, hopper's schema 2, turing without its .sha256: listed, not choosable"),
    ("stick-two-here", three()[:1] + [(LOVELACE_NEW, system(LOVELACE_NEW, "lovelace", "debian", "13", LOVELACE_DMI.upper(),
                                                            "2026-11-24T01:00:00Z", 3, "sftp://nas.local/backotter/lovelace-new"), "ok")],
     LOVELACE_DMI, None, False,
     "lovelace twice (reinstalled under a new install ID), both backed up from this computer: neither is taken for it"),
    ("stick-empty", [], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, False,
     "a stick no backup has written to yet: no machines"),
    ("stick-missing", three(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "no-partition", False,
     "no partition 3 on the boot disk (written by a file-copying tool): config missing"),
    ("stick-reformatted", three(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "reformat", False,
     "the config partition reformatted (another FAT serial): config damaged"),
]


def run(*cmd, **kw):
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1", PATH=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")
    return subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, **kw).stdout


def write(path, data, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    if mode is not None:
        os.chmod(path, mode)


def make_stick(work, machines, here, damage, api):
    """A Linux tree under WORK booted from a stick holding MACHINES; API is
    the bko-holt's `--version --json`, for the marker's dump_schemas (one
    from before them reads dump schema 1: --holt-api-unnamed-ok)."""
    img_dir = os.path.join(work, "img")
    write(os.path.join(work, "holt-api.json"), api)
    run(sys.executable, MARKER, "--platform", "linux", "--base-os", "ubuntu", "--base-release", "26.04.1",
        "--kernel", "7.0.0-30-generic", "--config-part", str(CONFIG_PART), "--config-mib", str(CONFIG_MIB),
        "--holt-api", os.path.join(work, "holt-api.json"), "--holt-api-unnamed-ok", img_dir)
    img = os.path.join(img_dir, "config.img")
    for mid, body, how in machines:
        d = os.path.join(work, "m", mid)
        write(os.path.join(d, "system.json"), body)
        digest = hashlib.sha256(body.encode()).hexdigest()
        if how == "bad":
            digest = hashlib.sha256(b"an older copy").hexdigest()
        if how != "none":
            write(os.path.join(d, "system.json.sha256"), "%s  system.json\n" % digest)
        run("mmd", "-i", img, "::/backotter/machines/" + mid)
        files = [os.path.join(d, f) for f in sorted(os.listdir(d))]
        run("mcopy", "-i", img, *files, "::/backotter/machines/%s/" % mid)
    if damage == "reformat":
        with open(img, "r+b") as f:
            f.seek(39)
            b = f.read(1)
            f.seek(39)
            f.write(bytes([b[0] ^ 0x5a]))
    tree = os.path.join(work, "tree")
    sectors = CONFIG_MIB * 2048
    total = START + sectors + 2048
    disk = os.path.join(tree, "dev", "sdc")
    os.makedirs(os.path.dirname(disk), exist_ok=True)
    with open(disk, "wb") as f:
        f.truncate(total * 512)
        with open(img, "rb") as src:
            f.seek(START * 512)
            shutil.copyfileobj(src, f, 1 << 20)
    sb = os.path.join(tree, "sys", "block")
    write(os.path.join(sb, "sdc", "size"), "%d\n" % total)
    write(os.path.join(sb, "sdc", "queue", "logical_block_size"), "512\n")
    parts = [(1, 64, 2048), (2, 2112, 1024)]
    if damage != "no-partition":
        parts.append((CONFIG_PART, START, sectors))
    for n, start, size in parts:
        p = os.path.join(sb, "sdc", "sdc%d" % n)
        write(os.path.join(p, "partition"), "%d\n" % n)
        write(os.path.join(p, "start"), "%d\n" % start)
        write(os.path.join(p, "size"), "%d\n" % size)
    write(os.path.join(sb, "nvme0n1", "size"), "2000409264\n")
    write(os.path.join(tree, "proc", "self", "mountinfo"),
          "29 1 7:0 / / ro - squashfs /dev/loop0 ro\n30 29 8:33 / /cdrom ro,noatime - iso9660 /dev/sdc1 ro\n")
    os.makedirs(os.path.join(tree, "cdrom", ".disk"), exist_ok=True)
    shutil.copy(os.path.join(img_dir, "backotter.json"), os.path.join(tree, "cdrom", ".disk", "backotter.json"))
    write(os.path.join(tree, "sys", "class", "dmi", "id", "product_uuid"), here + "\n")
    with open(os.path.join(img_dir, "backotter.json")) as f:
        return tree, json.load(f)


def call(out, name, argv, stdout, code, stdin=None, outfile=None):
    d = os.path.join(out, name)
    os.makedirs(d)
    write(os.path.join(d, "argv"), "".join(a + "\n" for a in argv))
    write(os.path.join(d, "stdout"), stdout)
    if code:
        write(os.path.join(d, "exit"), "%d\n" % code)
    if stdin is not None:
        write(os.path.join(d, "stdin"), stdin)
    if outfile is not None:
        write(os.path.join(d, "out"), outfile)


def main(argv):
    names = [x[0] for x in STICKS]
    if len(argv) < 2 or not os.access(argv[0], os.X_OK) or any(a not in names for a in argv[2:]):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    holt, out_dir = os.path.abspath(argv[0]), os.path.abspath(argv[1])
    only = set(argv[2:])
    version = subprocess.run([holt, "--version", "--json"], stdout=subprocess.PIPE, check=True).stdout
    ver = json.loads(version)["version"]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for scen, machines, here, damage, with_recovery, why, *more in STICKS:
        if only and scen not in only:
            continue
        copies = more[0] if more else []
        dest = os.path.join(out_dir, scen)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        with tempfile.TemporaryDirectory(prefix="bko-stick-") as work:
            tree, marker = make_stick(work, machines, here, damage, version)
            call(dest, "01-version", ["--version", "--json"], version, 0)
            p = subprocess.run([holt, "stick", "--json", "--sysroot", tree], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
            doc = json.loads(p.stdout)
            call(dest, "02-stick", ["stick", "--json"], p.stdout, p.returncode)
            print("%s: exit %d, config %s, %d machines" % (scen, p.returncode, doc["config"]["state"], len(doc["machines"])))
            if with_recovery:
                k = os.path.join(work, "k")
                os.mkdir(k, 0o700)
                for i, (name, typed) in enumerate(sorted(TYPED.items())):
                    o = os.path.join(k, str(i))
                    r = subprocess.run([holt, "recovery-words", o, "--json"], input=typed.encode(), stdout=subprocess.PIPE)
                    written = None
                    if os.path.exists(o):
                        with open(o, "rb") as f:
                            written = f.read()
                        os.remove(o)
                    call(dest, "%02d-recovery-%s" % (3 + i, name[3:]), ["recovery-words", "*", "--json"], r.stdout, r.returncode,
                         stdin=typed, outfile=written)
                    print("  recovery %s: exit %d" % (name, r.returncode))
            first = 3 + (len(TYPED) if with_recovery else 0)
            k = os.path.join(work, "copies")
            os.mkdir(k, 0o700)
            for i, (name, mid) in enumerate(copies):
                o = os.path.join(k, name)
                r = subprocess.run([holt, "stick", "--copy", mid, o, "--json", "--sysroot", tree], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
                written = None
                if os.path.exists(o):
                    with open(o, "rb") as f:
                        written = f.read()
                    os.remove(o)
                call(dest, "%02d-%s" % (first + i, name), ["stick", "--copy", mid, "*", "--json"], r.stdout, r.returncode,
                     outfile=written)
                print("  %s %s: exit %d, %s" % (name, mid, r.returncode, json.loads(r.stdout).get("code") or "written"))
        write(os.path.join(dest, "recorded.txt"),
              "Recorded by testvm/rescue/record-stick.py from bko-holt %s at %s: %s.\n"
              "The config partition made by stick/marker.py (image %s, serial %s, %d MiB, partition %d),\n"
              "machines put on it by mtools, read by `bko-holt stick --json --sysroot TREE` (argv kept as bko-rescue's).\n"
              % (ver, stamp, why, marker["image_id"], marker["config_partition"]["fat_serial"], CONFIG_MIB, CONFIG_PART)
              + ("The recovery-words calls answer by the words on standard input (the fake's `stdin`);\n"
                 "the good words' call writes the password to OUT (its `out`).\n" if with_recovery else "")
              + ("The stick --copy calls (argv `stick --copy ID * --json`) were run with --sysroot TREE too;\n"
                 "what one wrote to OUT is its `out`.\n" if copies else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
