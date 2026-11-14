#!/usr/bin/python3
"""Run in AnduinOS 2's live system, as root, by anduinos/install.sh: print the
installer's InstallPlan for "erase disk" as one JSON line, made the way its
GUI makes it (frontend.py's create_install_plan, the erase-disk path): the
wizard's state, the disk chosen from the GUI's own storage probe, its
topology bound, the platform probed, and installer_core.planning.build_plan,
with the password already hashed (crypt SHA-512, as passwords.hash_password
makes it).  anduinos-installer-executor reads that line on stdin.

Usage: plan.py DISK HOSTNAME USERNAME PASSWORD_HASH_FILE >plan.json
A summary goes to stderr; the plan (with the hash) only to stdout.
"""

import json
import sys

sys.path.insert(0, "/usr/lib/anduinos-installer-beta")

from frontend import probe_storage_inventory  # noqa: E402  (the GUI's probe; as root it runs parted itself)
from installer_core.planning import build_plan  # noqa: E402
from installer_core.probe import probe_platform  # noqa: E402
from installer_core.storage_inventory import bind_disk_topology  # noqa: E402
from languages import language_for_locale  # noqa: E402

LOCALE = "en_US.UTF-8"


def main() -> int:
    disk_path, hostname, username, hash_file = sys.argv[1:5]
    with open(hash_file, encoding="ascii") as f:
        password_hash = f.read().strip()
    language = language_for_locale(LOCALE)
    # main.py's initial shared state, with the choices a user makes for
    # "erase disk": Btrfs (the default) with its balanced compression, the
    # swap the installer sizes itself (no swap_size_mib), no updates at
    # install time, and the rest the wizard's defaults.
    state = {
        "storage_mode": "erase-disk",
        "filesystem": "btrfs",
        "btrfs_compression": "balanced",
        "locale": LOCALE,
        "timezone": "America/New_York",
        "keyboard": "us",
        "keyboard_variant": "",
        "hostname": hostname,
        "username": username,
        "full_name": "Test user",
        "sudo_without_password": False,
        "automatic_login": False,
        "ssh_password_login": False,
        "install_updates": False,
        "install_third_party_drivers": False,
        "install_multimedia_codecs": False,
        "input_methods": language.default_input_methods,
    }
    inventory = probe_storage_inventory()
    selected = next((d for d in inventory.disks if d.identity.path == disk_path), None)
    if selected is None:
        print(f"plan.py: no disk {disk_path} in the installer's inventory: "
              f"{[d.identity.path for d in inventory.disks]}", file=sys.stderr)
        return 1
    platform = probe_platform()
    plan = build_plan(
        state,
        selected.identity,
        platform,
        password_hash,
        disk_binding=bind_disk_topology(inventory, selected.identity.stable_id),
        inventory_digest=inventory.digest,
    )
    d = plan.to_dict()
    # The summary, without the identity's hash.
    storage = d.get("storage", {})
    summary = {
        "schema_version": d.get("schema_version"),
        "platform": d.get("platform"),
        "disk": storage.get("disk"),
        "filesystem": storage.get("filesystem"),
        "swap_size_mib": storage.get("swap_size_mib"),
        "btrfs_compression": storage.get("btrfs_compression"),
        "graph": storage.get("graph"),
        "boot": d.get("boot"),
        "software": d.get("software"),
    }
    print("plan.py: " + json.dumps(summary, sort_keys=True), file=sys.stderr)
    print(json.dumps(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
