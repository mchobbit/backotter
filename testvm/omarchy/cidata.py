#!/usr/bin/env python3
"""Write the files of an Omarchy autoinstall ("cidata") disc.

Usage: cidata.py DIR HOSTNAME DISK PASSWORD_HASH LUKS_PASSPHRASE AUTHORIZED_KEY

Omarchy's installer, finding a disc labelled cidata, skips its wizard and
takes the files the wizard would have written: user_configuration.json (an
archinstall configuration plus Omarchy's own settings) and
user_credentials.json, with user_encrypt_installation.txt and
authorized_keys.  These follow the templates in the ISO's /root/configurator
(Omarchy 4.0.6) for a full-disk, encrypted install: a 2 GiB EFI system
partition as /boot, and the rest LUKS-encrypted btrfs with Omarchy's
subvolumes.  The one user, tester, gets sudo and the same password as root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MIB = 1024 * 1024
GIB = 1024 * MIB
DISK_BYTES = 16 * GIB                  # make-omarchy.sh's disk


def size(value: int) -> dict:
    return {"sector_size": {"unit": "B", "value": 512}, "unit": "B", "value": value}


def main() -> int:
    out, hostname, disk, pw_hash, passphrase, key = sys.argv[1:7]
    boot_start, boot_size = MIB, 2 * GIB
    main_start = boot_start + boot_size
    main_size = DISK_BYTES - main_start - MIB          # the backup GPT's reserve
    root_id = "8c2c2b92-1070-455d-b76a-56263bab24aa"   # as the configurator names it
    config = {
        "app_config": None,
        "archinstall-language": "English",
        "auth_config": {},
        "audio_config": {"audio": "pipewire"},
        "bootloader_config": {"bootloader": "Limine", "uki": False, "removable": False},
        "custom_commands": [],
        "omarchy_install": {
            "input_method": "none",
            "input_xkb_layout": "",
            "mode": "full_disk",
            "defer_provisioning": False,
            "target_mount": "/mnt",
            "boot": {"esp_mount": "/boot", "esp_path": "/EFI/limine",
                     "efi_binary": "limine_x64.efi", "enable_fallback": True},
            "storage": {"kernel": "linux-omarchy"},
        },
        "disk_config": {
            "config_type": "default_layout",
            "device_modifications": [{
                "device": disk,
                "partitions": [
                    {"btrfs": [], "dev_path": None, "flags": ["boot", "esp"], "fs_type": "fat32",
                     "mount_options": [], "mountpoint": "/boot",
                     "obj_id": "ea21d3f2-82bb-49cc-ab5d-6f81ae94e18d",
                     "size": size(boot_size), "start": size(boot_start),
                     "status": "create", "type": "primary"},
                    {"btrfs": [{"mountpoint": "/", "name": "@"},
                               {"mountpoint": "/home", "name": "@home"},
                               {"mountpoint": "/var/log", "name": "@log"},
                               {"mountpoint": "/var/cache/pacman/pkg", "name": "@pkg"}],
                     "dev_path": None, "flags": [], "fs_type": "btrfs",
                     "mount_options": ["compress=zstd"], "mountpoint": None, "obj_id": root_id,
                     "size": size(main_size), "start": size(main_start),
                     "status": "create", "type": "primary"},
                ],
                "wipe": True,
            }],
            "disk_encryption": {"encryption_type": "luks", "lvm_volumes": [], "iter_time": 2000,
                                "partitions": [root_id], "encryption_password": passphrase},
        },
        "hostname": hostname,
        "kernels": ["linux-omarchy"],
        "network_config": {"type": "iso"},
        "ntp": True,
        "parallel_downloads": 8,
        "script": None,
        "services": [],
        "swap": True,
        "timezone": "UTC",
        "locale_config": {"kb_layout": "us", "sys_enc": "UTF-8", "sys_lang": "en_US.UTF-8"},
        "mirror_config": {
            "custom_repositories": [],
            "custom_servers": [{"url": "https://mirror.omarchy.org/$repo/os/$arch"},
                               {"url": "https://mirror.rackspace.com/archlinux/$repo/os/$arch"},
                               {"url": "https://geo.mirror.pkgbuild.com/$repo/os/$arch"}],
            "mirror_regions": {},
            "optional_repositories": [],
        },
        "packages": ["base-devel", "git", "omarchy-keyring", "omarchy-settings", "omarchy"],
        "profile_config": {"gfx_driver": None, "greeter": None, "profile": {}},
        "version": "3.0.9",
    }
    credentials = {
        "encryption_password": passphrase,
        "root_enc_password": pw_hash,
        "users": [{"enc_password": pw_hash, "groups": [], "sudo": True, "username": "tester"}],
    }
    d = Path(out)
    (d / "user_configuration.json").write_text(json.dumps(config, indent=4) + "\n")
    (d / "user_credentials.json").write_text(json.dumps(credentials, indent=4) + "\n")
    (d / "user_encrypt_installation.txt").write_text("true\n")
    (d / "user_full_name.txt").write_text("Test user\n")
    (d / "user_email_address.txt").write_text("tester@example.com\n")
    (d / "authorized_keys").write_text(key + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
