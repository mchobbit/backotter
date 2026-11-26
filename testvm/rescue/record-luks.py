#!/usr/bin/env python3
"""Records the fake's `luks-headers` scenario (holt-json.md's open question
12) from a real bko-holt and a real rustic-bko: `bko-holt luks-headers
LAYOUT DIR --repo REPO --password-file FILE --snapshot ID --json`, run as
bko-rescue runs it, against a repository that holds real LUKS2 header
backups where the backup tool keeps them (/.bko-holt/luks-headers/FILE in
the snapshot of the system).

    record-luks.py BKO_HOLT RUSTIC_BKO OUT_DIR

BKO_HOLT is a bko-holt built for this host (Linux), RUSTIC_BKO a rustic-bko
(BackOtter's); OUT_DIR gets the scenario directory `luks-headers`
(scenarios/ beside this file is where it lives).  Needs cryptsetup, run as
any user: two LUKS2 containers are made in image files (no device, no
root: luksFormat and luksHeaderBackup work on a file), with the UUIDs of
holt/tests/corpus/uc24-fde's ubuntu-save-enc and ubuntu-data-enc, so their
header files have the corpus dump's names.  Small headers (16 KiB of
metadata, 256 KiB of keyslots, PBKDF2) keep it quick; they are test
containers with a throwaway key, never a real system's, and nothing of them
is kept but the documents.

Two backups of one tree: the first with another header of ubuntu-data-enc
(a second luksFormat of the same UUID), the newest with the headers the
layout names.  The layout is uc24-fde's dump, its two luks_headers' size
and SHA-256 the newest headers'.  The calls:

    01-version         bko-holt --version --json
    02-fetch           the newest snapshot, the right password: exit 0,
                       both headers in DIR, byte for byte, 0700 and 0600
    03-mismatch        the older snapshot: header_mismatch, exit 1, no DIR
    04-wrong-password  rustic-bko's own refusal: header_fetch_failed, exit 1

02 and 03 hold a `password` file (the fake matches them only for it) and
their snapshot's ID in their argv; 04 any other call.  Then each fetched
header's `cryptsetup luksDump` UUID, and a restore of the newest snapshot
with load's glob file (LOAD_GLOBS, as load writes it for a layout that
kept headers and has no raw volume; json_cli.rs pins the same lines): the
tree comes back without the headers, their directory or /.bko-holt.
recorded.txt says what and when.  Python's stdlib only.

It works in a new directory in TMPDIR: run it with TMPDIR set to the
reserved RAM directory (cmp-run.sh --reserve).
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
CORPUS = os.path.join(HERE, "..", "..", "holt", "tests", "corpus", "uc24-fde", "src.json")
REPO_DIR = ".bko-holt/luks-headers"
# load's restore glob file for such a layout (holt/src/load/commands.rs).
LOAD_GLOBS = ["!/.bko-holt/luks-headers/**", "!/.bko-holt/luks-headers", "!/.bko-holt/system.json", "!/.bko-holt"]
PASSWORD = b"correct horse"
CRYPTSETUP = shutil.which("cryptsetup") or "/usr/sbin/cryptsetup"


def run(*cmd, ok=(0,), **kw):
    p = subprocess.run(list(cmd), capture_output=True, **kw)
    if p.returncode not in ok:
        sys.exit("record-luks: %s: exit %d: %s" % (" ".join(cmd), p.returncode, p.stderr.decode(errors="replace")[-600:]))
    return p


def private_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def container(work, name, uuid, key):
    img = os.path.join(work, name + ".img")
    with open(img, "wb") as f:
        f.truncate(4 << 20)
    run(CRYPTSETUP, "luksFormat", "--batch-mode", "--type", "luks2", "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000",
        "--luks2-metadata-size", "16k", "--luks2-keyslots-size", "256k", "--uuid", uuid, "--key-file", key, img)
    hdr = os.path.join(work, name + ".hdr")
    run(CRYPTSETUP, "luksHeaderBackup", img, "--header-backup-file", hdr)
    with open(hdr, "rb") as f:
        return f.read()


def call(out, name, argv, stdout, code, password=None):
    d = os.path.join(out, name)
    os.makedirs(d)
    with open(os.path.join(d, "argv"), "w") as f:
        f.write("\n".join(argv) + "\n")
    with open(os.path.join(d, "stdout"), "wb") as f:
        f.write(stdout)
    if code:
        with open(os.path.join(d, "exit"), "w") as f:
            f.write("%d\n" % code)
    if password is not None:
        with open(os.path.join(d, "password"), "wb") as f:
            f.write(password)


def main(argv):
    if len(argv) != 4:
        sys.exit("usage: record-luks.py BKO_HOLT RUSTIC_BKO OUT_DIR")
    holt, rustic, out_dir = (os.path.abspath(a) for a in argv[1:])
    out = os.path.join(out_dir, "luks-headers")
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    work = tempfile.mkdtemp(prefix="bko-record-luks-")
    try:
        key = os.path.join(work, "container-key")
        private_file(key, b"a throwaway test key")
        layout = json.load(open(CORPUS))
        heads = layout["storage"]["luks_headers"]
        names = {h["luks_uuid"]: h["file"] for h in heads}
        new = {h["file"]: container(work, "c%d" % i, h["luks_uuid"], key) for i, h in enumerate(heads)}
        data = [h for h in heads if h["label"] == "ubuntu-data-enc"][0]
        old = dict(new)
        old[data["file"]] = container(work, "old", data["luks_uuid"], key)
        assert old[data["file"]] != new[data["file"]] and len(old[data["file"]]) == len(new[data["file"]])
        for h in heads:
            h["bytes"] = len(new[h["file"]])
            h["sha256"] = hashlib.sha256(new[h["file"]]).hexdigest()
        lay = os.path.join(work, "layout.json")
        with open(lay, "w") as f:
            json.dump(layout, f, indent=2)
        # The trees backed up: the system's files, and the headers where
        # the backup tool keeps them.
        repo = os.path.join(work, "repo")
        pw = os.path.join(work, "password")
        private_file(pw, PASSWORD)
        rp = [rustic, "--repository", repo, "--password-file", pw, "--no-progress"]
        run(*rp, "init")
        for tag, files in (("older", old), ("newest", new)):
            src = os.path.join(work, "src-" + tag)
            os.makedirs(os.path.join(src, REPO_DIR), mode=0o700)
            os.makedirs(os.path.join(src, "etc"))
            with open(os.path.join(src, "etc", "hostname"), "w") as f:
                f.write("lovelace\n")
            for n, b in files.items():
                private_file(os.path.join(src, REPO_DIR, n), b)
            run(*rp, "backup", "--as-path", "/", "--host", "lovelace", "--label", tag, src)
            time.sleep(1.1)
        listed = json.loads(run(*rp, "snapshots", "--json").stdout)
        flat = []

        def walk(v):
            # A list of snapshots, or of groups holding them, either shape.
            if isinstance(v, dict) and "id" in v and "time" in v:
                flat.append(v)
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
        walk(listed)
        flat.sort(key=lambda s: s["time"])
        assert len(flat) == 2, flat
        older, newest = flat[0]["id"][:8], flat[-1]["id"][:8]
        snaps = (older, newest)
        # bko-rescue's directory: private, the password file in it.
        k = os.path.join(work, "k")
        os.mkdir(k, 0o700)
        os.chmod(k, 0o700)
        kpw = os.path.join(k, "password")
        private_file(kpw, PASSWORD)
        wrong = os.path.join(k, "wrong")
        private_file(wrong, b"not the password")
        api = run(holt, "--version", "--json")
        call(out, "01-version", ["--version", "--json"], api.stdout, 0)

        def fetch(dirname, snap, pwfile, ok):
            d = os.path.join(k, dirname)
            p = run(holt, "luks-headers", lay, d, "--repo", repo, "--password-file", pwfile, "--archiver", rustic, "--snapshot", snap, "--json",
                    ok=(ok,), env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"})
            return d, p
        d, p = fetch("h", newest, kpw, 0)
        doc = json.loads(p.stdout)
        assert doc["format"] == "bko-holt-luks-headers" and len(doc["headers"]) == 2, doc
        assert (os.stat(d).st_mode & 0o7777) == 0o700
        dumps = []
        for h in doc["headers"]:
            got = open(h["path"], "rb").read()
            assert got == new[h["file"]], h["file"]
            assert (os.stat(h["path"]).st_mode & 0o7777) == 0o600
            uuid = [l.split()[-1] for l in run(CRYPTSETUP, "luksDump", h["path"]).stdout.decode().splitlines() if l.startswith("UUID:")]
            assert uuid and names[uuid[0]] == h["file"], (uuid, h["file"])
            dumps.append("%s: %d bytes, luksDump UUID %s" % (h["file"], len(got), uuid[0]))
        # The scenario's argv is bko-rescue's: the layout, DIR, the repo
        # and the password file as `*`, the snapshot's ID as recorded.
        pat = lambda snap: ["luks-headers", "*", "*", "--repo", "*", "--password-file", "*", "--snapshot", snap, "--json"]
        call(out, "02-fetch", pat(newest), p.stdout, 0, PASSWORD)
        d3, p3 = fetch("h3", older, kpw, 1)
        assert not os.path.exists(d3) and json.loads(p3.stdout)["code"] == "header_mismatch", p3.stdout
        call(out, "03-mismatch", pat(older), p3.stdout, 1, PASSWORD)
        d4, p4 = fetch("h4", newest, wrong, 1)
        assert not os.path.exists(d4) and json.loads(p4.stdout)["code"] == "header_fetch_failed", p4.stdout
        call(out, "04-wrong-password", pat("*"), p4.stdout, 1)
        # The restore leaves the headers' copies out (load's glob file),
        # and their directories: every path listed, directories too.
        globs = os.path.join(work, "restore.globs")
        with open(globs, "w") as f:
            f.write("".join(g + "\n" for g in LOAD_GLOBS))
        target = os.path.join(work, "target")
        run(*rp, "restore", "--glob-file", globs, newest, target)
        left = sorted(os.path.relpath(os.path.join(r, n), target) for r, ds, fs in os.walk(target) for n in ds + fs)
        assert "etc/hostname" in left and not any(x.startswith(".bko-holt") for x in left), left
        ver = lambda *c: run(*c).stdout.decode().strip().splitlines()[0]
        with open(os.path.join(out, "recorded.txt"), "w") as f:
            f.write("Recorded %s by record-luks.py on %s:\n" % (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), os.uname().nodename))
            f.write("  bko-holt %s (%s)\n  %s\n  %s\n" % (json.loads(api.stdout)["version"], holt, ver(rustic, "--version"), ver(CRYPTSETUP, "--version")))
            f.write("Snapshots: %s (older, another ubuntu-data-enc header), %s (newest, the layout's headers).\n" % snaps)
            f.write("Fetched by 02-fetch, each checked by bko-holt and here byte for byte:\n")
            for x in dumps:
                f.write("  %s\n" % x)
            f.write("03-mismatch: %s\n04-wrong-password: %s\n" % (json.loads(p3.stdout)["text"], json.loads(p4.stdout)["text"]))
            f.write("A restore of %s with load's glob file (%s) brought back %s: no header, no .bko-holt.\n" % (newest, " ".join(LOAD_GLOBS), ", ".join(left)))
        print(open(os.path.join(out, "recorded.txt")).read(), end="")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv)
