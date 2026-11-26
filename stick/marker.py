#!/usr/bin/env python3
"""Make a rescue image's marker and its config partition, for every platform.

Usage: marker.py --platform P --base-os OS --base-release R --kernel K
                 --config-part N --holt-api FILE
                 [--zfs-version V --zfs-features FILE] OUT_DIR

Writes into OUT_DIR (see docs/design/stick-config.md):
  backotter.json   the image marker, for /.disk/backotter.json in the image
  stick.json       the stick's identity, as shipped (stick_id null)
  config.img       256 MiB of FAT16 labelled BACKOTTER, with a random volume
                   serial, holding backotter/stick.json, an empty
                   backotter/machines/, and backotter/settings.toml and
                   README.txt from config-partition/; the image builder makes
                   it partition N
and prints the image ID.  --zfs-features is OpenZFS's own list for the
release (/usr/share/zfs/compatibility.d/openzfs-X.Y); without --zfs-version
the marker says the image has no OpenZFS.  --holt-api is what the image's
own bko-holt prints to `bko-holt --version --json`: its `dump_schemas` (the
dump schemas its load reads) go into the marker, so the backup tool can warn
when a stick can't read a machine's dump.  A bko-holt from before 2026-11-26
names none; it reads dump schema 1 only, and the marker says [1].

Needs: python3 3.11 or later, mkfs.vfat (dosfstools), mtools.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import shutil
import subprocess
import sys
import tomllib
import uuid

# Every image's, since 2026-11-26 (holt-json.md's open question 13): a
# system.json up to stick.rs's SYSTEM_MAX (64 MiB), the one before it, and
# each machine's 20 MiB of restore logs.  Still FAT16, which reaches 2 GiB.
CONFIG_MIB = 256
CONFIG_LABEL = "BACKOTTER"
SYSTEM_SCHEMAS = [1]            # system.json schemas the stick can read
DUMP_SCHEMAS_UNNAMED = [1]      # what a bko-holt that names no dump_schemas reads
# What the image's bko-holt must list in its api document's features:
# luks_headers, so a machine whose dump kept LUKS2 headers can be restored
# (holt-json.md's open question 12; an older stick --copy refuses it).
REQUIRED_FEATURES = ("luks_headers",)
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg: str) -> None:
    sys.exit(f"marker: {msg}")


def write_json(path: str, doc: dict) -> None:
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def dump_schemas(path: str, unnamed_ok: bool = False) -> list[int]:
    """The dump schemas bko-holt load reads, from PATH: that bko-holt's
    `--version --json` document (bko-holt-api, holt-json.md).  A bko-holt
    from before dump_schemas is refused, since its image would fail the boot
    check dump-schemas, and one whose features lack REQUIRED_FEATURES (from
    before 3100b25), unless UNNAMED_OK (record-stick.py's recordings): then
    one with no dump_schemas reads DUMP_SCHEMAS_UNNAMED."""
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        die(f"--holt-api {path}: not bko-holt --version --json's document ({e})")
    if not isinstance(doc, dict) or doc.get("format") != "bko-holt-api":
        die(f"--holt-api {path}: not a bko-holt-api document (a bko-holt from before RT10?)")
    if "dump_schemas" not in doc:
        if not unnamed_ok:
            die(f"--holt-api {path}: bko-holt {doc.get('version')} names no dump_schemas (it predates them), "
                "and its image would fail the boot check dump-schemas: refresh holt-bin (img-refresh-holt.sh) "
                "to a bko-holt at or after ae051fb")
        print(f"marker: bko-holt {doc.get('version')} names no dump_schemas (it predates them): "
              f"it reads dump schema {DUMP_SCHEMAS_UNNAMED[0]} only", file=sys.stderr)
        return list(DUMP_SCHEMAS_UNNAMED)
    s = doc["dump_schemas"]
    if not (isinstance(s, list) and s and all(type(x) is int and x > 0 for x in s)):
        die(f"--holt-api {path}: dump_schemas isn't a list of schema numbers ({s!r})")
    have = doc.get("features")
    missing = [f for f in REQUIRED_FEATURES if not (isinstance(have, list) and f in have)]
    if missing and not unnamed_ok:
        die(f"--holt-api {path}: bko-holt {doc.get('version')} lists no {', '.join(missing)} in its features, "
            "so its image couldn't restore a machine whose dump kept LUKS2 headers (holt-json.md's open "
            "question 12): refresh holt-bin (img-refresh-holt.sh) to a bko-holt at or after 3100b25")
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--base-os", required=True, help="what the image is built from, e.g. ubuntu")
    ap.add_argument("--base-release", required=True, help="e.g. 26.04.1 or 15.1-RELEASE")
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--config-part", type=int, required=True, help="the config partition's number")
    ap.add_argument("--config-mib", type=int, default=CONFIG_MIB,
                    help="the config partition's size (FAT16 reaches 2 GiB)")
    ap.add_argument("--config-bin", action="append", default=[], metavar="FILE",
                    help="a program to put in the config partition's backotter/bin/")
    ap.add_argument("--holt-api", required=True, metavar="FILE",
                    help="the image's `bko-holt --version --json`, for the marker's dump_schemas")
    ap.add_argument("--holt-api-unnamed-ok", action="store_true",
                    help="take a bko-holt from before dump_schemas as reading dump schema 1 (recordings only; "
                         "an image build refuses it)")
    ap.add_argument("--zfs-version")
    ap.add_argument("--zfs-features")
    ap.add_argument("out_dir")
    a = ap.parse_args()
    if bool(a.zfs_version) != bool(a.zfs_features):
        die("--zfs-version and --zfs-features go together")
    for c in ("mkfs.vfat", "mmd", "mcopy"):
        if not shutil.which(c, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin"):
            die(f"missing {c}")
    dumps = dump_schemas(a.holt_api, a.holt_api_unnamed_ok)
    os.makedirs(a.out_dir, exist_ok=True)
    config_sectors = a.config_mib * 2048

    zfs = None
    if a.zfs_version:
        with open(a.zfs_features) as f:
            features = sorted(l.strip() for l in f if l.strip() and not l.startswith("#"))
        if not features:
            die(f"{a.zfs_features} lists no features")
        zfs = {"version": a.zfs_version, "features": features}

    image_id = str(uuid.uuid4())
    serial = secrets.token_hex(4).upper()
    built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(os.path.join(a.out_dir, "backotter.json"), {
        "format": "backotter-image", "schema": 1, "image_id": image_id,
        "built": built, "platform": a.platform,
        "base": {"os": a.base_os, "release": a.base_release}, "kernel": a.kernel,
        "zfs": zfs, "system_schemas": SYSTEM_SCHEMAS, "dump_schemas": dumps,
        "config_partition": {"number": a.config_part, "sectors": config_sectors,
                             "label": CONFIG_LABEL, "fat_serial": f"{serial[:4]}-{serial[4:]}"},
    })
    stick_json = os.path.join(a.out_dir, "stick.json")
    write_json(stick_json, {"format": "backotter-stick", "schema": 1,
                            "image_id": image_id, "stick_id": None})

    settings = os.path.join(HERE, "config-partition", "settings.toml")
    readme = os.path.join(HERE, "config-partition", "README.txt")
    with open(settings, "rb") as f:
        tomllib.load(f)     # the shipped text must parse

    img = os.path.join(a.out_dir, "config.img")
    if os.path.exists(img):
        os.remove(img)
    with open(img, "wb") as f:
        f.truncate(config_sectors * 512)
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1",
               PATH=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")
    run = lambda *cmd: subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL)
    run("mkfs.vfat", "-F", "16", "-n", CONFIG_LABEL, "-i", serial, img)
    run("mmd", "-i", img, "::/backotter", "::/backotter/machines")
    run("mcopy", "-i", img, stick_json, settings, "::/backotter/")
    run("mcopy", "-i", img, readme, "::/")
    if a.config_bin:
        run("mmd", "-i", img, "::/backotter/bin")
        run("mcopy", "-i", img, *a.config_bin, "::/backotter/bin/")
    print(image_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
