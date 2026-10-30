#!/usr/bin/env python3
"""Make a rescue image's marker and its config partition, for every platform.

Usage: marker.py --platform P --base-os OS --base-release R --kernel K
                 --config-part N [--zfs-version V --zfs-features FILE] OUT_DIR

Writes into OUT_DIR (see docs/design/stick-config.md):
  backotter.json   the image marker, for /.disk/backotter.json in the image
  stick.json       the stick's identity, as shipped (stick_id null)
  config.img       32 MiB of FAT16 labelled BACKOTTER, with a random volume
                   serial, holding backotter/stick.json, an empty
                   backotter/machines/, and backotter/settings.toml and
                   README.txt from config-partition/; the image builder makes
                   it partition N
and prints the image ID.  --zfs-features is OpenZFS's own list for the
release (/usr/share/zfs/compatibility.d/openzfs-X.Y); without --zfs-version
the marker says the image has no OpenZFS.

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

CONFIG_SECTORS = 65536          # 32 MiB; FAT32 would need more than that
CONFIG_LABEL = "BACKOTTER"
SYSTEM_SCHEMAS = [1]            # system.json schemas the stick can read
PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos")
HERE = os.path.dirname(os.path.abspath(__file__))


def die(msg: str) -> None:
    sys.exit(f"marker: {msg}")


def write_json(path: str, doc: dict) -> None:
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--platform", required=True, choices=PLATFORMS)
    ap.add_argument("--base-os", required=True, help="what the image is built from, e.g. ubuntu")
    ap.add_argument("--base-release", required=True, help="e.g. 26.04.1 or 15.1-RELEASE")
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--config-part", type=int, required=True, help="the config partition's number")
    ap.add_argument("--zfs-version")
    ap.add_argument("--zfs-features")
    ap.add_argument("out_dir")
    a = ap.parse_args()
    if bool(a.zfs_version) != bool(a.zfs_features):
        die("--zfs-version and --zfs-features go together")
    for c in ("mkfs.vfat", "mmd", "mcopy"):
        if not shutil.which(c, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin"):
            die(f"missing {c}")
    os.makedirs(a.out_dir, exist_ok=True)

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
        "zfs": zfs, "system_schemas": SYSTEM_SCHEMAS,
        "config_partition": {"number": a.config_part, "sectors": CONFIG_SECTORS,
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
        f.truncate(CONFIG_SECTORS * 512)
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1",
               PATH=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")
    run = lambda *cmd: subprocess.run(cmd, check=True, env=env, stdout=subprocess.DEVNULL)
    run("mkfs.vfat", "-F", "16", "-n", CONFIG_LABEL, "-i", serial, img)
    run("mmd", "-i", img, "::/backotter", "::/backotter/machines")
    run("mcopy", "-i", img, stick_json, settings, "::/backotter/")
    run("mcopy", "-i", img, readme, "::/")
    print(image_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
