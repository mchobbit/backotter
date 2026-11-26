#!/usr/bin/env python3
"""Writes the app's hand-made fixtures (RT11): rustic-bko's answers for
fake-rustic-bko (`rustic/`), and `scenarios/app-three`, stick-three's
recorded calls (its stick --copy refused: no layout) with report-bug's
beside them.  Run again after changing
it; the files are committed.  Python's stdlib only.

rustic/NAME/ calls, in order (fake-bko-holt's rules):
  01-config     cat config with the right password (its `password`)
  02-snapshots  snapshots --json with it
  09-wrong      either, any other password: rustic_core's C002, exit 1
"""

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = b"correct horse"
# What the recovery key's words stand for: stick-three's 03-recovery-good
# writes this to OUT (the fake writes its `out` file as it is).
RECOVERY = b"legal winner thank year wave sausage worth useful legal winner thank yellow"

WRONG = ("[ERROR] The password that has been entered, seems to be incorrect. No suitable key found "
         "for the given password. Please check your password and try again. [C002]\n")

LOVELACE = [
    ("2026-11-22T02:00:03+01:00", 61_200_000_000, [], "a7f3c901"),
    ("2026-11-21T02:00:02+01:00", 61_000_000_000, [], "3be04d2a"),
    ("2026-11-20T02:00:05+01:00", 60_900_000_000, [], "d1185e77"),
    ("2026-11-15T02:00:01+01:00", 60_400_000_000, ["weekly"], "4e6f1d08"),
    ("2026-11-01T02:00:04+01:00", 59_800_000_000, ["monthly"], "c03a77b2"),
    ("2026-10-01T02:00:02+02:00", 58_900_000_000, ["monthly"], "5d2e90af"),
    ("2026-09-01T02:00:00+02:00", 58_100_000_000, ["monthly"], "9a0b1c2d"),
    ("2026-08-01T02:00:00+02:00", 57_300_000_000, ["monthly"], "8b1c2d3e"),
    ("2026-07-01T02:00:00+02:00", 56_000_000_000, ["monthly"], "7c2d3e4f"),
    ("2026-06-01T02:00:00+02:00", 55_200_000_000, ["monthly"], "6d3e4f50"),
    ("2026-05-01T02:00:00+02:00", 54_900_000_000, ["monthly"], "5e4f5061"),
    ("2026-04-01T02:00:00+02:00", 53_700_000_000, ["monthly"], "4f506172"),
    ("2026-03-01T02:00:00+01:00", 52_000_000_000, ["monthly"], "30617283"),
    ("2026-02-01T02:00:00+01:00", 51_100_000_000, ["monthly"], "21728394"),
]

SHARED = [
    ("lovelace", "2026-11-22T02:00:03+01:00", 61_200_000_000, [], "a7f3c901"),
    ("babbage", "2026-11-21T03:00:00-05:00", 120_400_000_000, [], "b0bba9e1"),
    ("lovelace", "2026-11-21T02:00:02+01:00", 61_000_000_000, [], "3be04d2a"),
    ("babbage", "2026-11-14T03:00:00-05:00", 119_800_000_000, ["weekly"], "b0bba9e2"),
]


def snap(host, t, size, tags, short):
    return {"time": t, "program_version": "rustic v0.12.0+backotter.4", "tree": "ee" * 32, "paths": ["/"],
            "hostname": host, "username": "root", "uid": 0, "gid": 0, "tags": tags,
            "summary": {"total_files_processed": 184233, "total_bytes_processed": size},
            "id": (short + "5f" * 28)[:64]}


def write(d, name, data):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def rustic(name, url, repo_id, snaps):
    top = os.path.join(HERE, "rustic", name)
    shutil.rmtree(top, ignore_errors=True)
    pre = "--repository\n%s\n--password-file\n*\n--no-progress\n" % url
    config = {"version": 2, "id": repo_id, "chunker_polynomial": "3da3358b4dc173", "is_hot": None}
    for call, pw, what, out in [("01-config", PASSWORD, "cat\nconfig\n", json.dumps(config, indent=2)),
                                ("02-snapshots", PASSWORD, "snapshots\n--json\n", json.dumps(snaps, indent=2)),
                                ("03-config-recovery", RECOVERY, "cat\nconfig\n", json.dumps(config, indent=2)),
                                ("04-snapshots-recovery", RECOVERY, "snapshots\n--json\n", json.dumps(snaps, indent=2))]:
        d = os.path.join(top, call)
        write(d, "argv", pre + what)
        write(d, "password", pw)
        write(d, "stdout", out + "\n")
    for call, what in [("09-wrong-config", "cat\nconfig\n"), ("09-wrong-snapshots", "snapshots\n--json\n")]:
        d = os.path.join(top, call)
        write(d, "argv", pre + what)
        write(d, "stderr", WRONG)
        write(d, "exit", "1\n")


def grouped(host, items):
    return [{"group_key": {"hostname": host, "label": "", "paths": ["/"]}, "snapshots": items}]


def main():
    rustic("lovelace", "sftp://nas.local/backotter/lovelace", "fb1e7ec987523d2c" + "0a" * 24,
           grouped("lovelace", [snap("lovelace", *x) for x in LOVELACE]))
    # Another repository's ID: the stick's lovelace isn't this one.
    rustic("mismatch", "sftp://nas.local/backotter/lovelace", "0123456789abcdef" + "0b" * 24,
           grouped("lovelace", [snap("lovelace", *x) for x in LOVELACE[:3]]))
    rustic("shared", "sftp://nas.local/backotter/shared", "5ba7ed0000000000" + "0c" * 24,
           [snap(*x) for x in SHARED])
    # The app's holt scenario: stick-three's calls, and report-bug's.
    app = os.path.join(HERE, "scenarios", "app-three")
    shutil.rmtree(app, ignore_errors=True)
    shutil.copytree(os.path.join(HERE, "scenarios", "stick-three"), app)
    os.remove(os.path.join(app, "recorded.txt"))
    write(app, "README", "stick-three's recorded calls (version, stick, recovery-words, stick --copy's no_layout), and report-bug's, "
                         "hand-written: written by make-app-fixtures.py for app-test.py.\n")
    report = os.path.join(app, "08-report")
    write(report, "argv", "report-bug\n--output\n*\n--strip-file\n*\n")
    write(report, "out", "BackOtter bug report (fake-bko-holt's)\nhost-1: a redacted report\n")
    write(report, "stderr", "bko-holt report-bug: (the fake's report)\n")
    report = os.path.join(app, "09-report-unredacted")
    write(report, "argv", "report-bug\n--output\n*\n--no-redact\n")
    write(report, "out", "BackOtter bug report, NOT redacted (fake-bko-holt's)\nlovelace: an unredacted report\n")
    # A dry run's: the api says the lock took (no_commit, dry_run).
    dry = os.path.join(HERE, "scenarios", "app-dry")
    shutil.rmtree(dry, ignore_errors=True)
    shutil.copytree(app, dry)
    write(dry, "README", "app-three's calls, its api document saying BKO_HOLT_NO_COMMIT took (no_commit, dry_run "
                         "true), as a dry run's: written by make-app-fixtures.py for app-test.py.\n")
    v = os.path.join(dry, "01-version", "stdout")
    with open(v) as f:
        doc = json.load(f)
    doc.update(no_commit=True, dry_run=True)
    write(os.path.dirname(v), "stdout", json.dumps(doc, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
