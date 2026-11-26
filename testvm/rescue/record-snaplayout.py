#!/usr/bin/env python3
"""Records the fake's `snapshot-layout` scenario (holt-json.md's open
question 15) from a real bko-holt and a real rustic-bko: `bko-holt
snapshot-layout OUT --repo REPO --password-file FILE --snapshot ID
--install-id ID --json`, run as bko-rescue runs it, against a repository
whose snapshots hold the machine's system.json where the backup tool keeps
it (/.bko-holt/system.json, beside /.bko-holt/luks-headers).

    record-snaplayout.py BKO_HOLT RUSTIC_BKO OUT_DIR [RESTIC_BKO]

BKO_HOLT is a bko-holt built for this host (Linux), RUSTIC_BKO a
rustic-bko (BackOtter's), RESTIC_BKO a restic-bko when its words for a
path a snapshot doesn't hold are to be recorded too (run by hand: holt's
restic-bko argv can't run yet, its `--repository`); OUT_DIR gets the
scenario directory
`snapshot-layout` (scenarios/ beside this file is where it lives).  Needs
cryptsetup, run as any user: LUKS2 containers are made in image files (no
device, no root), with the UUIDs of holt/tests/corpus/uc24-fde's
ubuntu-save-enc and ubuntu-data-enc, as record-luks.py makes them.  They
are test containers with a throwaway key, never a real system's, and
nothing of them is kept but the documents.

Three backups of one machine (stick-three's lovelace, its install ID), each
a staging root backed up with `--as-path /`, as open question 14's answer
has the backup tool do:

    before   the system's files only: a backup from before the backup tool
             kept system.json in each snapshot
    older    system.json (sequence 40) whose layout is uc24-fde's dump with
             the older headers' sizes and SHA-256, and those headers
    newest   system.json (sequence 41), another ubuntu-data-enc header (a
             passphrase changed, say), and its headers

The calls:

    01-version         bko-holt --version --json
    02-older           the older snapshot's own layout: exit 0, OUT the
                       older system.json's layout, byte for byte as dump
                       prints it
    03-older-headers   luks-headers with 02's OUT, the older snapshot: exit
                       0, both headers the older ones (the point of open
                       question 15: the older backup can be restored)
    04-newest          the newest snapshot's own layout: exit 0
    05-before          the backup from before: no_system_json, exit 1
    06-other-machine   --install-id another machine's: system_refused
    07-wrong-password  rustic-bko's own refusal: system_fetch_failed
    08-headers-only    a backup that kept headers but no system.json (open
                       question 12's, before 15): no_system_json, from
                       rustic-bko's "Node `system.json` not found"

02 to 06 hold a `password` file (the fake matches them only for it) and
their snapshot's ID in their argv; 07 any other call; each `out` is what
OUT got.  Then a restore of the older snapshot with load's glob file (no
raw volume), and of a fourth backup (no header) with load's four `--glob`
arguments: no header, no system.json and no .bko-holt come back.  Then a
richer tree (modes, a setuid file, a sticky directory, a hard link, a
symbolic link, a FIFO, an empty directory, set times, `.bko-holtx` and
`etc/.bko-holt`, which aren't the backup tool's) restored with the four
`--glob` arguments and without any: the same tree, the same root
directory, but for /.bko-holt (load's globs change nothing else in a
restore).  recorded.txt says what and when.  Python's stdlib only.

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
BKO = ".bko-holt"
INSTALL_ID = "0b7e3c52-8d1f-4a6e-9c3b-2f5d7a1e4b90"
OTHER_ID = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5"
LOAD_GLOBS = ["!/.bko-holt/luks-headers/**", "!/.bko-holt/luks-headers", "!/.bko-holt/system.json", "!/.bko-holt"]
# And its --glob arguments when nothing else is left out (commands.rs):
# the same four, whatever the layout kept.
LOAD_ARGS = list(LOAD_GLOBS)
PASSWORD = b"correct horse"
CRYPTSETUP = shutil.which("cryptsetup") or "/usr/sbin/cryptsetup"


def run(*cmd, ok=(0,), **kw):
    p = subprocess.run(list(cmd), capture_output=True, **kw)
    if p.returncode not in ok:
        sys.exit("record-snaplayout: %s: exit %d: %s" % (" ".join(cmd), p.returncode, p.stderr.decode(errors="replace")[-600:]))
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


def call(out, name, argv, stdout, code, password=None, out_file=None):
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
    if out_file is not None:
        shutil.copyfile(out_file, os.path.join(d, "out"))


def snapshots(rp):
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
    return [s["id"][:8] for s in flat]


def main(argv):
    if len(argv) not in (4, 5):
        sys.exit("usage: record-snaplayout.py BKO_HOLT RUSTIC_BKO OUT_DIR [RESTIC_BKO]")
    holt, rustic, out_dir = (os.path.abspath(a) for a in argv[1:4])
    restic = os.path.abspath(argv[4]) if len(argv) == 5 else None
    out = os.path.join(out_dir, "snapshot-layout")
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    work = tempfile.mkdtemp(prefix="bko-record-snaplayout-")
    try:
        key = os.path.join(work, "container-key")
        private_file(key, b"a throwaway test key")
        base = json.load(open(CORPUS))
        heads = base["storage"]["luks_headers"]
        data = [h for h in heads if h["label"] == "ubuntu-data-enc"][0]
        older = {h["file"]: container(work, "c%d" % i, h["luks_uuid"], key) for i, h in enumerate(heads)}
        newest = dict(older)
        newest[data["file"]] = container(work, "new", data["luks_uuid"], key)
        assert newest[data["file"]] != older[data["file"]]

        def layout_of(files):
            lay = json.loads(json.dumps(base))
            for h in lay["storage"]["luks_headers"]:
                h["bytes"] = len(files[h["file"]])
                h["sha256"] = hashlib.sha256(files[h["file"]]).hexdigest()
            return lay

        def system(seq, lay):
            # The envelope (stick-config.md's system.json), the dump whole
            # in `layout`; no secret in it.
            return {"format": "backotter-system", "schema": 1, "sequence": seq,
                    "written": "2026-11-%02dT02:00:03Z" % (seq - 20), "written_by": "record-snaplayout.py",
                    "machine": {"install_id": INSTALL_ID, "hostname": "lovelace"},
                    "os": {"id": "ubuntu-core", "version_id": "24"},
                    "backup": {"engine": "rustic", "repository": {"kind": "local", "url": "/srv/bko"}},
                    "layout": lay}
        lays = {"older": layout_of(older), "newest": layout_of(newest)}
        repo = os.path.join(work, "repo")
        pw = os.path.join(work, "password")
        private_file(pw, PASSWORD)
        rp = [rustic, "--repository", repo, "--password-file", pw, "--no-progress"]
        run(*rp, "init")
        # Each backup: a staging root S, the system's files and the backup
        # tool's own under /.bko-holt, backed up as / (--as-path).
        for tag, seq, files in (("before", None, None), ("older", 40, older), ("newest", 41, newest)):
            s = os.path.join(work, "stage-" + tag)
            os.makedirs(os.path.join(s, "etc"))
            with open(os.path.join(s, "etc", "hostname"), "w") as f:
                f.write("lovelace\n")
            if files is not None:
                os.makedirs(os.path.join(s, BKO, "luks-headers"), mode=0o700)
                for n, b in files.items():
                    private_file(os.path.join(s, BKO, "luks-headers", n), b)
                with open(os.path.join(s, BKO, "system.json"), "w") as f:
                    json.dump(system(seq, lays[tag]), f, indent=2)
            run(*rp, "backup", "--as-path", "/", "--host", "lovelace", "--label", tag, s)
            time.sleep(1.1)
        snaps = snapshots(rp)
        assert len(snaps) == 3, snaps
        before, old_id, new_id = snaps
        k = os.path.join(work, "k")
        os.mkdir(k, 0o700)
        os.chmod(k, 0o700)
        kpw = os.path.join(k, "password")
        private_file(kpw, PASSWORD)
        wrong = os.path.join(k, "wrong")
        private_file(wrong, b"not the password")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
        api = run(holt, "--version", "--json")
        assert "snapshot_layout" in json.loads(api.stdout)["documents"], api.stdout
        call(out, "01-version", ["--version", "--json"], api.stdout, 0)

        def snap_layout(name, snap, pwfile, ok, install_id=INSTALL_ID):
            o = os.path.join(k, name + ".json")
            p = run(holt, "snapshot-layout", o, "--repo", repo, "--password-file", pwfile, "--archiver", rustic,
                    "--snapshot", snap, "--install-id", install_id, "--json", ok=(ok,), env=env)
            return o, p
        pat = lambda snap, i=INSTALL_ID: ["snapshot-layout", "*", "--repo", "*", "--password-file", "*", "--snapshot", snap + "*", "--install-id", i, "--json"]
        said = {}
        for name, snap, tag in (("02-older", old_id, "older"), ("04-newest", new_id, "newest")):
            o, p = snap_layout(name, snap, kpw, 0)
            doc = json.loads(p.stdout)
            got = open(o, "rb").read()
            assert doc["format"] == "bko-holt-snapshot-layout" and doc["luks_headers"] == 2 and doc["install_id"] == INSTALL_ID, doc
            assert json.loads(got) == lays[tag] and doc["bytes"] == len(got), name
            assert (os.stat(o).st_mode & 0o7777) == 0o600
            said[name] = "%s: OUT %d bytes, sequence %s, %d headers kept; the same JSON as the %s system.json's layout" % (
                name, len(got), doc["sequence"], doc["luks_headers"], tag)
            call(out, name, pat(snap), p.stdout, 0, PASSWORD, o)
            if name == "02-older":
                # Its headers from the same snapshot: the older ones.
                d = os.path.join(k, "h")
                h = run(holt, "luks-headers", o, d, "--repo", repo, "--password-file", kpw, "--archiver", rustic,
                        "--snapshot", snap, "--json", env=env)
                hd = json.loads(h.stdout)
                for r in hd["headers"]:
                    assert open(r["path"], "rb").read() == older[r["file"]], r["file"]
                said["03-older-headers"] = "03-older-headers: %d headers fetched from %s, each the older backup's, byte for byte" % (len(hd["headers"]), snap)
                call(out, "03-older-headers", ["luks-headers", "*", "*", "--repo", "*", "--password-file", "*", "--snapshot", snap + "*", "--json"],
                     h.stdout, 0, PASSWORD)
                # The stick's copy would be the newest's: its headers don't
                # match this snapshot (luks-headers' header_mismatch).
                newest_lay = os.path.join(work, "newest-layout.json")
                with open(newest_lay, "w") as f:
                    json.dump(lays["newest"], f, indent=2)
                m = run(holt, "luks-headers", newest_lay, os.path.join(k, "hm"), "--repo", repo, "--password-file", kpw,
                        "--archiver", rustic, "--snapshot", snap, "--json", ok=(1,), env=env)
                assert json.loads(m.stdout)["code"] == "header_mismatch", m.stdout
                said["mismatch"] = "The newest layout (the stick's copy) against %s: header_mismatch, as before open question 15." % snap
        o, p = snap_layout("05-before", before, kpw, 1)
        assert not os.path.exists(o) and json.loads(p.stdout)["code"] == "no_system_json", p.stdout
        call(out, "05-before", pat(before), p.stdout, 1, PASSWORD)
        o, p = snap_layout("06-other-machine", new_id, kpw, 1, OTHER_ID)
        assert not os.path.exists(o) and json.loads(p.stdout)["code"] == "system_refused", p.stdout
        call(out, "06-other-machine", pat(new_id, OTHER_ID), p.stdout, 1, PASSWORD)
        o, p = snap_layout("07-wrong-password", new_id, wrong, 1)
        assert not os.path.exists(o) and json.loads(p.stdout)["code"] == "system_fetch_failed", p.stdout
        call(out, "07-wrong-password", ["snapshot-layout", "*", "--repo", "*", "--password-file", "*", "--snapshot", "*", "--install-id", "*", "--json"], p.stdout, 1)
        # load's glob file for a layout that kept headers: nothing of the
        # backup tool's staging comes back, system.json included.
        globs = os.path.join(work, "restore.globs")
        with open(globs, "w") as f:
            f.write("".join(g + "\n" for g in LOAD_GLOBS))
        target = os.path.join(work, "target")
        run(*rp, "restore", "--glob-file", globs, old_id, target)
        left = sorted(os.path.relpath(os.path.join(r, n), target) for r, ds, fs in os.walk(target) for n in ds + fs)
        assert "etc/hostname" in left and not any(x.startswith(BKO) for x in left), left
        # A layout that kept no header: load passes the two on the command
        # line (--glob), with no glob file.  A fourth backup, made after
        # the calls above: system.json and the system's files only.
        s = os.path.join(work, "stage-plain")
        os.makedirs(os.path.join(s, "etc"))
        os.makedirs(os.path.join(s, BKO))
        with open(os.path.join(s, "etc", "hostname"), "w") as f:
            f.write("lovelace\n")
        with open(os.path.join(s, BKO, "system.json"), "w") as f:
            json.dump(system(42, json.load(open(CORPUS))), f, indent=2)
        run(*rp, "backup", "--as-path", "/", "--host", "lovelace", "--label", "plain", s)
        plain = snapshots(rp)[-1]
        target2 = os.path.join(work, "target2")
        args = [x for g in LOAD_ARGS for x in ("--glob", g)]
        run(*rp, "restore", *args, plain, target2)
        left2 = sorted(os.path.relpath(os.path.join(r, n), target2) for r, ds, fs in os.walk(target2) for n in ds + fs)
        assert "etc/hostname" in left2 and not any(x.startswith(BKO) for x in left2), left2
        # A backup that kept headers but no system.json (open question
        # 12's, before 15): .bko-holt is there, system.json isn't.
        s = os.path.join(work, "stage-headers-only")
        os.makedirs(os.path.join(s, "etc"))
        os.makedirs(os.path.join(s, BKO, "luks-headers"), mode=0o700)
        with open(os.path.join(s, "etc", "hostname"), "w") as f:
            f.write("lovelace\n")
        for n, b in older.items():
            private_file(os.path.join(s, BKO, "luks-headers", n), b)
        run(*rp, "backup", "--as-path", "/", "--host", "lovelace", "--label", "headers-only", s)
        hdr_only = snapshots(rp)[-1]
        o, p = snap_layout("08-headers-only", hdr_only, kpw, 1)
        doc = json.loads(p.stdout)
        assert not os.path.exists(o) and doc["code"] == "no_system_json" and "Node `system.json` not found in tree." in doc["text"], p.stdout
        call(out, "08-headers-only", pat(hdr_only), p.stdout, 1, PASSWORD)
        # restic-bko's words for the same two, its dump run by hand.
        restic_said = []
        if restic:
            for what, snap in (("no .bko-holt", before), ("no system.json", hdr_only)):
                r = run(restic, "-r", repo, "-p", pw, "--no-cache", "dump", snap, "/" + BKO + "/system.json", ok=(1,), env=env)
                line = [l for l in r.stderr.decode(errors="replace").splitlines() if l.strip()][-1].strip()
                assert line.endswith("not found in snapshot") and ('path "/%s"' % BKO in line or 'path "/%s/system.json"' % BKO in line), line
                restic_said.append("%s (%s): %s" % (snap, what, line))
        # load's globs change nothing else in a restore: a richer tree,
        # restored with them and without any.
        s = os.path.join(work, "stage-rich")
        for d in ("etc", "srv/empty", "tmp", ".bko-holtx", "etc/.bko-holt", BKO):
            os.makedirs(os.path.join(s, d), exist_ok=True)
        for rel, body, mode in (("etc/hostname", b"lovelace\n", 0o644), ("etc/shadow-ish", b"x\n", 0o640),
                                ("srv/run.sh", b"#!/bin/sh\n", 0o4755), (".bko-holtx/keep", b"mine\n", 0o600),
                                ("etc/.bko-holt/keep", b"mine too\n", 0o644)):
            with open(os.path.join(s, rel), "wb") as f:
                f.write(body)
            os.chmod(os.path.join(s, rel), mode)
        os.link(os.path.join(s, "etc/hostname"), os.path.join(s, "etc/hostname.hard"))
        os.symlink("../etc/hostname", os.path.join(s, "srv/hostname.link"))
        os.mkfifo(os.path.join(s, "srv/fifo"), 0o620)
        os.chmod(os.path.join(s, "tmp"), 0o1777)
        os.chmod(os.path.join(s, "srv/empty"), 0o711)
        with open(os.path.join(s, BKO, "system.json"), "w") as f:
            json.dump(system(43, json.load(open(CORPUS))), f, indent=2)
        for r, ds, fs in os.walk(s, topdown=False):
            for n in fs + ds:
                os.utime(os.path.join(r, n), ns=(1700000000123456789, 1700000000123456789), follow_symlinks=False)
        os.chmod(s, 0o750)
        os.utime(s, ns=(1700000001000000000, 1700000001000000000))
        run(*rp, "backup", "--as-path", "/", "--host", "lovelace", "--label", "rich", s)
        rich = snapshots(rp)[-1]

        def tree(t):
            rows = {}
            for r, ds, fs in os.walk(t):
                for n in ds + fs:
                    pth = os.path.join(r, n)
                    st = os.lstat(pth)
                    rel = os.path.relpath(pth, t)
                    body = ""
                    if os.path.islink(pth):
                        body = os.readlink(pth)
                    elif os.path.isfile(pth):
                        body = hashlib.sha256(open(pth, "rb").read()).hexdigest()
                    rows[rel] = (oct(st.st_mode), st.st_nlink if not os.path.isdir(pth) else 0, st.st_size if os.path.isfile(pth) else 0,
                                 st.st_mtime_ns, st.st_uid, st.st_gid, body)
            st = os.stat(t)
            return rows, (oct(st.st_mode), st.st_mtime_ns, st.st_uid, st.st_gid)
        t_globs = os.path.join(work, "target-rich-globs")
        t_none = os.path.join(work, "target-rich-none")
        run(*rp, "restore", "--numeric-id", *args, rich, t_globs)
        run(*rp, "restore", "--numeric-id", rich, t_none)
        (rows_g, root_g), (rows_n, root_n) = tree(t_globs), tree(t_none)
        assert not any(k == BKO or k.startswith(BKO + "/") for k in rows_g), sorted(rows_g)
        assert BKO + "/system.json" in rows_n, sorted(rows_n)
        rest_n = {k: v for k, v in rows_n.items() if not (k == BKO or k.startswith(BKO + "/"))}
        diff = sorted(k for k in set(rows_g) | set(rest_n) if rows_g.get(k) != rest_n.get(k))
        assert not diff, [(k, rows_g.get(k), rest_n.get(k)) for k in diff]
        # The root directory: the same in both but for its time, which is
        # the restore's own in both (rustic-bko 0.12.0+backotter.4 sets
        # neither the target's mode nor its time from the snapshot's root,
        # globs or none; recorded.txt says what each was).
        assert (root_g[0], root_g[2], root_g[3]) == (root_n[0], root_n[2], root_n[3]), (root_g, root_n)
        src_root = (oct(os.stat(s).st_mode), os.stat(s).st_mtime_ns)
        assert ".bko-holtx/keep" in rows_g and "etc/.bko-holt/keep" in rows_g, sorted(rows_g)
        ver = lambda *c: run(*c).stdout.decode().strip().splitlines()[0]
        with open(os.path.join(out, "recorded.txt"), "w") as f:
            f.write("Recorded %s by record-snaplayout.py on %s:\n" % (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), os.uname().nodename))
            f.write("  bko-holt %s (%s)\n  %s\n  %s\n" % (json.loads(api.stdout)["version"], holt, ver(rustic, "--version"), ver(CRYPTSETUP, "--version")))
            f.write("Snapshots: %s (before: no system.json), %s (older: sequence 40), %s (newest: sequence 41, another ubuntu-data-enc header).\n" % (before, old_id, new_id))
            for n in ("02-older", "03-older-headers", "04-newest", "mismatch"):
                f.write(said[n] + "\n")
            for n in ("05-before", "06-other-machine", "07-wrong-password", "08-headers-only"):
                f.write("%s: %s\n" % (n, json.loads(open(os.path.join(out, n, "stdout")).read())["text"]))
            f.write("A restore of %s with load's glob file (%s) brought back %s: no header, no system.json, no .bko-holt.\n" % (old_id, " ".join(LOAD_GLOBS), ", ".join(left)))
            f.write("A fourth backup %s (system.json, no header), restored with load's %s: brought back %s.\n" % (plain, " ".join("--glob '%s'" % g for g in LOAD_ARGS), ", ".join(left2)))
            f.write("A richer backup %s (%d paths beside /.bko-holt: modes, setuid, sticky, a hard link, a symbolic link, a FIFO, an empty directory, set times, .bko-holtx, etc/.bko-holt), restored --numeric-id with load's four --glob arguments and without any: the same %d paths (type, mode, links, size, mtime, owner, contents); only /.bko-holt left out.\n" % (
                rich, len(rest_n), len(rows_g)))
            f.write("Its root directory, backed up as %s with mtime %d: restored as %s with mtime %d (globs) and %s with mtime %d (none), each the restore's own time: the archiver sets neither from the snapshot's root, globs or none.\n" % (
                src_root[0], src_root[1], root_g[0], root_g[1], root_n[0], root_n[1]))
            if restic:
                f.write("  %s\n" % ver(restic, "version"))
                for l in restic_said:
                    f.write("restic-bko dump by hand of %s\n" % l)
        print(open(os.path.join(out, "recorded.txt")).read(), end="")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv)
