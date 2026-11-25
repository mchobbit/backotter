"""BackShot1 on TrueNAS 25.10: its middleware writes /etc from its
configuration database (/data/freenas-v1.db) at every boot, so bs1-adopt puts
the hostname, the fixed ed25519 host key, ssh.service at boot and mDNS in
that database (truenas_hostname, truenas_ssh, truenas_mdns), and stamps the
boot it last changed it in (truenas_stamp); bs1-backup's truenas_adopted
wants those settings there and a later boot.  The restored Thinkpad came up
as "truenas" with the install's own host key and ssh.service disabled, since
adopt had written /etc itself (debian-12-20261125-170305, 2026-11-25).  And
bs1-manifest's TrueNAS lists (the files a boot writes again from the
database, with the same bytes or not) and its --db-dump and --db-compare,
which bs1-backup and bs1-verify's truenas_db_same use to compare the
database by its rows.  A fake midclt, a real SQLite; no root."""

import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if os.geteuid() == 0:
    raise unittest.SkipTest("BackShot1's tests don't run as root")

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from test_bs1_truenas import BASH, BS1, assignment, exe, functions  # noqa: E402

PUB = b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixedFixedFixedFixedFixedFixedFixedFixed root@bs1-thinkpad\n"
KEY = b"-----BEGIN OPENSSH PRIVATE KEY-----\nfixed\n-----END OPENSSH PRIVATE KEY-----\n"
OLD_PUB = b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINDWSCIVw8/ysUHKaP/HarM6Ou9MydP3PJIR/PAGnlCE root@localhost\n"
OLD_KEY = b"-----BEGIN OPENSSH PRIVATE KEY-----\ninstall's\n-----END OPENSSH PRIVATE KEY-----\n"


def b64(b):
    return base64.b64encode(b).decode()


# The middleware, as far as BackShot1 asks it: its state in a JSON file, each
# call appended to "calls".
MIDCLT = r'''
import base64, json, os, sys
state_path = os.environ["FAKE_MID"]
s = json.load(open(state_path))
a = sys.argv[1:]
if a[:1] != ["call"]:
    sys.exit(f"midclt {a}: not faked")
m, args = a[1], [json.loads(x) if x[:1] in "[{" else x for x in a[2:]]
s["calls"].append([m] + args)
out = None
if m == "network.configuration.config":
    out = s["net"]
elif m == "network.configuration.update":
    s["net"].update(args[0])
    out = s["net"]
elif m == "datastore.config" and args == ["services.ssh"]:
    out = s["ssh"]
elif m == "ssh.save_keys":
    if not s.get("save_broken"):
        d = os.environ["FAKE_SSH"]
        for f, col in (("ssh_host_ed25519_key", "ssh_host_ed25519_key"), ("ssh_host_ed25519_key.pub", "ssh_host_ed25519_key_pub")):
            p = os.path.join(d, f)
            if os.path.exists(p):
                s["ssh"][col] = base64.b64encode(open(p, "rb").read()).decode()
elif m == "etc.generate":
    if args == ["ssh"]:
        open(os.environ["FAKE_SSHD"], "w").write(f"Port 22\nPermitRootLogin {s['permit']}\nPubkeyAuthentication yes\n")
elif m == "service.query":
    out = {"id": 11, "service": "ssh", "enable": s["ssh_enable"], "state": "RUNNING"}
elif m == "service.update" and args[0] == "ssh":
    if not s.get("update_broken"):
        s["ssh_enable"] = args[1]["enable"]
    out = 11
else:
    sys.exit(f"midclt call {m}: not faked")
json.dump(s, open(state_path, "w"))
if out is not None:
    print(json.dumps(out))
'''

# GNU base64's -w0, which macOS's lacks.
BASE64 = "import base64, sys\nassert sys.argv[1:] == ['-w0'], sys.argv\nsys.stdout.write(base64.b64encode(sys.stdin.buffer.read()).decode())\n"


@unittest.skipUnless(BASH, "needs bash")
class Middleware(unittest.TestCase):
    def setUp(self):
        self.t = t = Path(tempfile.mkdtemp(prefix="bs1-truenas-cfg-"))
        self.addCleanup(shutil.rmtree, t, True)
        b = self.bin = t / "bin"
        b.mkdir()
        exe(b / "midclt", f"#!{sys.executable}\n{MIDCLT}")
        exe(b / "base64", f"#!{sys.executable}\n{BASE64}")
        (t / "keys").mkdir()
        (t / "keys" / "ssh_host_ed25519_key").write_bytes(KEY)
        (t / "keys" / "ssh_host_ed25519_key.pub").write_bytes(PUB)
        # bs1-adopt has installed the fixed key's files in /etc/ssh by then.
        (t / "ssh").mkdir()
        shutil.copy(t / "keys" / "ssh_host_ed25519_key", t / "ssh")
        shutil.copy(t / "keys" / "ssh_host_ed25519_key.pub", t / "ssh")
        (t / "version").write_text("25.10.8")
        (t / "boot_id").write_text("e57d2f53-3f2e-4528-969a-df9df00ce679\n")
        # The restored Thinkpad's, before this bs1-adopt.
        self.state = {"net": {"id": 1, "hostname": "truenas", "domain": "local",
                              "service_announcement": {"netbios": True, "mdns": True, "wsd": True}},
                      "ssh": {"id": 1, "ssh_host_ed25519_key": b64(OLD_KEY), "ssh_host_ed25519_key_pub": b64(OLD_PUB),
                              "ssh_host_rsa_key_pub": "c3NoLXJzYQ=="},
                      "ssh_enable": False, "permit": "without-password", "calls": []}
        self.save()
        common, adopt = BS1 / "bs1-common", BS1 / "bs1-adopt"
        self.adopt = "\n".join([
            "set -euo pipefail",
            "SOURCE_HOST=thinkpad",
            f"keys={t}/keys",
            f"TRUENAS_STAMP={t}/etc/backshot1/truenas-adopt",
            f"BOOT_ID={t}/boot_id",
            assignment(adopt, "truenas_changed"),
            f"TRUENAS_SSHD_CONFIG={t}/sshd_config",
            functions(adopt, "tn_get", "truenas_hostname", "truenas_ssh", "truenas_mdns", "truenas_stamp"),
        ])
        self.backup = "\n".join([
            "set -uo pipefail",
            "SOURCE_HOST=thinkpad",
            f"HERE={t}",
            f"TRUENAS_VERSION={t}/version",
            f"TRUENAS_STAMP={t}/etc/backshot1/truenas-adopt",
            f"BOOT_ID={t}/boot_id",
            functions(common, "is_truenas"),
            functions(BS1 / "bs1-backup", "truenas_adopted"),
        ])

    def save(self):
        (self.t / "mid.json").write_text(json.dumps(self.state))

    def mid(self):
        return json.loads((self.t / "mid.json").read_text())

    def sh(self, lib, script):
        t = self.t
        return subprocess.run([BASH, "-c", f"{lib}\n{script}"], capture_output=True, text=True, timeout=60,
                              env={"PATH": f"{self.bin}:/usr/bin:/bin", "LC_ALL": "C", "TMPDIR": str(t),
                                   "FAKE_MID": str(t / "mid.json"), "FAKE_SSH": str(t / "ssh"), "FAKE_SSHD": str(t / "sshd_config")})

    def adopt_all(self):
        return self.sh(self.adopt, 'truenas_hostname; truenas_ssh; truenas_mdns; truenas_stamp; echo "changed $truenas_changed"')

    def reboot(self, boot="2994b09e-17d3-4fd5-bc8f-2552d711b099"):
        (self.t / "boot_id").write_text(boot + "\n")

    # ---- bs1-adopt

    def test_adopt_puts_the_settings_in_the_database_and_asks_for_a_reboot(self):
        r = self.adopt_all()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.mid()
        self.assertEqual(s["net"]["hostname"], "thinkpad")
        self.assertEqual(base64.b64decode(s["ssh"]["ssh_host_ed25519_key_pub"]), PUB)
        self.assertEqual(base64.b64decode(s["ssh"]["ssh_host_ed25519_key"]), KEY)
        self.assertIs(s["ssh_enable"], True)
        methods = [c[0] for c in s["calls"]]
        self.assertIn("ssh.save_keys", methods)
        self.assertIn(["etc.generate", "ssh"], s["calls"])
        self.assertIn(["service.update", "ssh", {"enable": True}], s["calls"])
        # mDNS was on: only the hostname is updated.
        self.assertEqual([c for c in s["calls"] if c[0] == "network.configuration.update"],
                         [["network.configuration.update", {"hostname": "thinkpad"}]])
        stamp = (self.t / "etc" / "backshot1" / "truenas-adopt").read_text()
        self.assertIn("boot_id=e57d2f53-3f2e-4528-969a-df9df00ce679\n", stamp)
        self.assertIn("TrueNAS: reboot now (systemctl reboot)", r.stdout)
        self.assertIn("changed yes", r.stdout)

    def test_adopt_again_changes_nothing_and_keeps_the_stamp(self):
        self.adopt_all()
        self.reboot()
        before = (self.t / "etc" / "backshot1" / "truenas-adopt").read_text()
        self.state = self.mid()
        self.state["calls"] = []
        self.save()
        r = self.adopt_all()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unchanged: the middleware's hostname is thinkpad", r.stdout)
        self.assertIn("unchanged: the middleware's ed25519 host key is the fixed one", r.stdout)
        self.assertIn("unchanged: the middleware starts ssh at boot", r.stdout)
        self.assertIn("unchanged: the middleware announces mDNS", r.stdout)
        self.assertIn("TrueNAS: this boot is a later one than adopt's last change", r.stdout)
        self.assertIn("changed no", r.stdout)
        self.assertEqual((self.t / "etc" / "backshot1" / "truenas-adopt").read_text(), before)
        methods = [c[0] for c in self.mid()["calls"]]
        for m in ("network.configuration.update", "ssh.save_keys", "service.update"):
            self.assertNotIn(m, methods)

    def test_adopt_stamps_a_later_boot_when_it_changes_something(self):
        self.adopt_all()
        self.reboot()
        self.state = self.mid()
        self.state["net"]["hostname"] = "truenas"
        self.save()
        r = self.adopt_all()
        self.assertIn("TrueNAS: reboot now", r.stdout)
        self.assertIn("boot_id=2994b09e", (self.t / "etc" / "backshot1" / "truenas-adopt").read_text())

    def test_adopt_turns_mdns_on_keeping_the_other_announcements(self):
        self.state["net"]["service_announcement"] = {"netbios": True, "mdns": False, "wsd": False}
        self.save()
        r = self.adopt_all()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.mid()["net"]["service_announcement"], {"netbios": True, "mdns": True, "wsd": False})

    def test_adopt_stops_if_save_keys_doesnt_take_the_key(self):
        self.state["save_broken"] = True
        self.save()
        r = self.adopt_all()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ssh.save_keys didn't put the fixed host key in the middleware's database", r.stderr)
        self.assertFalse((self.t / "etc" / "backshot1" / "truenas-adopt").exists())

    def test_adopt_stops_if_ssh_stays_off_at_boot(self):
        self.state["update_broken"] = True
        self.save()
        r = self.adopt_all()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the middleware still doesn't start ssh at boot", r.stderr)

    def test_adopt_stops_if_root_may_not_log_in_by_key(self):
        self.state["permit"] = "no"
        self.save()
        r = self.adopt_all()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the middleware's sshd_config doesn't let root in by key", r.stderr)

    def test_adopt_takes_root_by_password_too(self):
        self.state["permit"] = "yes"
        self.save()
        self.assertEqual(self.adopt_all().returncode, 0)

    def test_adopt_leaves_the_middleware_alone_elsewhere(self):
        # The main flow calls these only when bs1-common's is_truenas says so.
        text = (BS1 / "bs1-adopt").read_text()
        self.assertIn('if (. "$here/bs1-common" && is_truenas); then truenas=yes; fi', text)
        self.assertIn("    if [[ $truenas == yes ]]; then\n        truenas_hostname\n        return\n    fi", text)
        self.assertIn("    if [[ $truenas == yes ]]; then\n        echo \"== TrueNAS: the host key and ssh.service in the middleware's database\"\n"
                      "        truenas_ssh\n    else\n        allow_root_key_login\n    fi", text)
        self.assertIn("[[ $truenas == no ]] || truenas_mdns", text)

    # ---- bs1-backup truenas_adopted

    def adopted(self):
        return self.sh(self.backup, "truenas_adopted; echo rc $?")

    def test_preflight_passes_after_adopt_and_a_reboot(self):
        self.adopt_all()
        self.reboot()
        r = self.adopted()
        self.assertEqual(r.stdout, "rc 0\n", r.stderr)

    def test_preflight_wants_a_reboot_after_adopt(self):
        self.adopt_all()
        r = self.adopted()
        self.assertIn("rc 1", r.stdout)
        self.assertIn("bs1-adopt changed TrueNAS's configuration in this boot: reboot first", r.stdout)

    def test_preflight_wants_adopt(self):
        r = self.adopted()
        self.assertIn("rc 1", r.stdout)
        self.assertIn("bs1-adopt hasn't put BackShot1's settings in its middleware's database", r.stdout)

    def test_preflight_names_each_setting_the_middleware_would_undo(self):
        self.adopt_all()
        self.reboot()
        self.state = self.mid()
        self.state["net"]["hostname"] = "truenas"
        self.state["net"]["service_announcement"]["mdns"] = False
        self.state["ssh"]["ssh_host_ed25519_key_pub"] = b64(OLD_PUB)
        self.state["ssh_enable"] = False
        self.save()
        r = self.adopted()
        self.assertIn("rc 1", r.stdout)
        for why in ("its hostname is 'truenas', not 'thinkpad'", "it doesn't announce mDNS",
                    "its ed25519 host key isn't", "it doesn't start ssh at boot"):
            self.assertIn(why, r.stdout)
        self.assertEqual(sorted(os.listdir(self.t)), sorted(["bin", "boot_id", "etc", "keys", "mid.json", "ssh", "sshd_config", "version"]),
                         "its temporary files are gone")

    def test_preflight_says_nothing_elsewhere(self):
        (self.t / "version").unlink()
        r = self.adopted()
        self.assertEqual(r.stdout, "rc 0\n")
        self.assertEqual(self.mid()["calls"], [])


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


M = load("bs1_manifest_truenas_under_test", BS1 / "bs1-manifest")

FILE, LINK, DIR = 0o100644, 0o120777, 0o40755


def row(rel, content="x", mode=FILE, mtime=1, uid=0, gid=0):
    """A manifest row: CONTENT is a file's bytes (its hash is what's kept) or a link's target."""
    extra = content if stat.S_ISLNK(mode) else ("-" if stat.S_ISDIR(mode) else M.hashlib.sha256(content.encode()).hexdigest())
    size = len(content) if not stat.S_ISDIR(mode) else 2
    cols = [rel, oct(mode), str(uid), str(gid), str(size), str(mtime * 10**9), extra, "-"]
    if stat.S_ISREG(mode):
        cols += ["-", "-"]
    return "\t".join(cols) + "\n"


# Of the Thinkpad's baseline (debian-12-20261125-170305), as it was then.
TRUENAS_TREE = {
    "usr/bin/midclt": "#!/usr/bin/python3\n",
    "data/freenas-v1.db": "SQLite format 3\0 pages",
    "data/user-services.json": '{"ssh": true}',
    "etc/hostname": "thinkpad",
    "etc/certificates/truenas_default.crt": "-----BEGIN CERTIFICATE-----",
    "etc/certificates/truenas_default.key": "-----BEGIN PRIVATE KEY-----",
    "etc/cron.d/middlewared": "8 3 * * * root midclt call update.download",
    "etc/netdata/netdata.conf": "dbengine multihost disk space MB = 399",
    "etc/nut/upsmon.conf": "MONITOR",
    "etc/ssh/ssh_host_ed25519_key": "fixed private",
    "etc/ssh/ssh_host_ed25519_key.pub": "ssh-ed25519 fixed",
    "etc/ssh/sshd_config": "PermitRootLogin without-password",
    "etc/hosts": "127.0.0.1 thinkpad",
    "audit/MIDDLEWARE.db": "audit rows",
    "audit/MIDDLEWARE.db-wal": "wal",
    "audit/syslog-ng-00002.rqf": "queue",
    "var/db/system/netdata/netdata-meta.db": "metrics",
    "var/db/system/nfs/rmtab": "",
    "var/db/system/samba4/private/netlogon_creds_cli.tdb": "tdb",
}


# Written again only when they would change (mako), or never: a boot leaves
# their mtimes.
STILL = {"usr/bin/midclt", "audit/MIDDLEWARE.db", "etc/hosts", "etc/ssh/sshd_config"}


class ManifestLists(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-truenas-man-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def compare(self, old, new):
        a, b = self.t / "old.tsv", self.t / "new.tsv"
        a.write_text("".join(old))
        b.write_text("".join(new))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = M.compare(str(a), str(b))
        return rc, out.getvalue()

    def tree(self, mtime=1, tree=TRUENAS_TREE, **changes):
        rows = []
        for rel, content in tree.items():
            content = changes.pop(rel, content)
            if content is not None:
                rows.append(row(rel, content, mtime=1 if rel in STILL else mtime))
        rows += [row(rel, content, mtime=mtime) for rel, content in changes.items() if content is not None]
        return rows

    def test_a_boot_rewriting_the_middlewares_files_is_expected(self):
        # Every file written again (a later mtime); the database's pages, the
        # random cron time, netdata's sizing and the runtime records with
        # other bytes; netdata and the audit queues made anew.
        new = self.tree(mtime=2, **{"data/freenas-v1.db": "SQLite format 3\0 other pages",
                                    "etc/cron.d/middlewared": "32 4 * * * root midclt call update.download",
                                    "etc/netdata/netdata.conf": "dbengine multihost disk space MB = 357",
                                    "audit/MIDDLEWARE.db-wal": "more wal", "audit/syslog-ng-00002.rqf": "more",
                                    "var/db/system/netdata/netdata-meta.db": "more metrics",
                                    "audit/SMB.db-shm": "shm", "var/db/system/netdata/context-meta.db-wal": "w"})
        rc, out = self.compare(self.tree(), new)
        self.assertEqual(rc, 0, out)
        self.assertIn("TrueNAS: written again from the middleware's database at every boot", out)

    def test_what_adopt_used_to_write_itself_is_still_caught(self):
        # debian-12-20261125-170305: the hostname, the host key and
        # sshd_config came back as the middleware's, not adopt's.
        rc, out = self.compare(self.tree(), self.tree(mtime=2, **{"etc/hostname": "truenas",
                                                                  "etc/ssh/ssh_host_ed25519_key.pub": "ssh-ed25519 install's",
                                                                  "etc/ssh/sshd_config": "Port 22"}))
        self.assertEqual(rc, 1)
        for p in ("changed: etc/hostname", "changed: etc/ssh/ssh_host_ed25519_key.pub", "changed: etc/ssh/sshd_config"):
            self.assertIn(p, out)

    def test_a_changed_certificate_is_caught(self):
        rc, out = self.compare(self.tree(), self.tree(mtime=2, **{"etc/certificates/truenas_default.crt": "a new one"}))
        self.assertEqual(rc, 1)
        self.assertIn("changed: etc/certificates/truenas_default.crt", out)

    def test_the_database_must_come_back(self):
        rc, out = self.compare(self.tree(), self.tree(**{"data/freenas-v1.db": None}))
        self.assertEqual(rc, 1)
        self.assertIn("missing: data/freenas-v1.db", out)

    def test_the_database_must_keep_its_mode(self):
        old = self.tree()
        new = [r for r in self.tree() if not r.startswith("data/freenas-v1.db\t")] + [
            row("data/freenas-v1.db", "SQLite format 3\0 other pages", mode=0o100644, uid=1000)]
        rc, out = self.compare(old, new)
        self.assertEqual(rc, 1)
        self.assertIn("changed: data/freenas-v1.db", out)

    def test_the_audit_databases_themselves_are_held(self):
        rc, out = self.compare(self.tree(), self.tree(**{"audit/MIDDLEWARE.db": "fewer rows"}))
        self.assertEqual(rc, 1)
        self.assertIn("changed: audit/MIDDLEWARE.db", out)

    def test_a_first_boots_missing_nfs_files_are_caught(self):
        # A baseline from a fresh install's first boot (no system.globalid
        # yet) lacks them; bs1-adopt's reboot is what makes them.
        rc, out = self.compare(self.tree(), self.tree(**{"etc/nfs.conf": "[nfsd]"}))
        self.assertEqual(rc, 1)
        self.assertIn("added: etc/nfs.conf", out)

    def test_none_of_it_holds_elsewhere(self):
        plain = {k: v for k, v in TRUENAS_TREE.items() if k != "usr/bin/midclt"}
        rc, out = self.compare(self.tree(tree=plain), self.tree(mtime=2, tree=plain, **{"etc/cron.d/middlewared": "32 4"}))
        self.assertEqual(rc, 1)
        self.assertNotIn("TrueNAS:", out)
        for p in ("changed: data/freenas-v1.db", "changed: etc/cron.d/middlewared", "changed: etc/certificates/truenas_default.crt",
                  "changed: etc/hostname", "changed: data/user-services.json"):
            self.assertIn(p, out)


class Database(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="bs1-truenas-db-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def db(self, name, rows, order=1):
        p = self.t / name
        con = sqlite3.connect(p)
        con.executescript("CREATE TABLE network_globalconfiguration (id INTEGER PRIMARY KEY, gc_hostname TEXT, gc_blob BLOB);"
                          "CREATE TABLE services_services (id INTEGER PRIMARY KEY, srv_service TEXT, srv_enable INTEGER);"
                          "CREATE TABLE storage_disk (disk_identifier TEXT PRIMARY KEY, disk_expiretime TEXT);")
        con.execute("INSERT INTO network_globalconfiguration VALUES (1, ?, ?)", (rows.get("hostname", "thinkpad"), b"\x00\xff"))
        services = [(11, "ssh", rows.get("ssh", 1)), (12, "nfs", 0)][::order]
        con.executemany("INSERT INTO services_services VALUES (?, ?, ?)", services)
        con.execute("INSERT INTO storage_disk VALUES ('{serial}S7XM', ?)", (rows.get("expire"),))
        con.commit()
        con.close()
        return p

    def dump(self, db):
        out = self.t / (db.name + ".txt")
        M.db_dump(str(db), str(out))
        return out

    def compare(self, a, b, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = M.db_compare(str(a), str(b), **kw)
        return rc, out.getvalue()

    def test_the_same_rows_dump_the_same_however_the_pages_lie(self):
        a = self.db("a.db", {})
        b = self.db("b.db", {}, order=-1)
        con = sqlite3.connect(b)
        con.execute("PRAGMA user_version = 0")
        con.execute("VACUUM")
        con.close()
        self.assertNotEqual(a.read_bytes(), b.read_bytes())
        da, db = self.dump(a), self.dump(b)
        self.assertEqual(da.read_text(), db.read_text())
        rc, out = self.compare(da, db)
        self.assertEqual(rc, 0, out)
        self.assertIn("rows compared: 4 in 3 tables; differing: 0", out)
        self.assertIn('"gc_blob": {"blob": "AP8="}', da.read_text())

    def test_a_lost_setting_is_caught_and_named(self):
        rc, out = self.compare(self.dump(self.db("a.db", {})), self.dump(self.db("b.db", {"hostname": "truenas", "ssh": 0})))
        self.assertEqual(rc, 1)
        self.assertIn("row network_globalconfiguration: 1 only before, 1 only after", out)
        self.assertIn('"gc_hostname": "truenas"', out)
        self.assertIn("row services_services: 1 only before, 1 only after", out)
        self.assertIn("differing: 2", out)

    def test_a_schema_change_is_caught(self):
        a = self.dump(self.db("a.db", {}))
        b = self.db("b.db", {})
        con = sqlite3.connect(b)
        con.execute("ALTER TABLE storage_disk ADD COLUMN disk_model TEXT")
        con.commit()
        con.close()
        rc, out = self.compare(a, self.dump(b))
        self.assertEqual(rc, 1)
        self.assertIn("schema storage_disk:", out)

    def test_a_volatile_table_is_left_out(self):
        a, b = self.dump(self.db("a.db", {})), self.dump(self.db("b.db", {"expire": "2026-11-25"}))
        self.assertEqual(self.compare(a, b)[0], 1)
        rc, out = self.compare(a, b, volatile=["storage_disk"])
        self.assertEqual(rc, 0, out)
        self.assertIn("leaving out storage_disk", out)

    def test_no_table_is_volatile_yet(self):
        # Three boots of the Thinkpad's TrueNAS changed no row (2026-11-25).
        self.assertEqual(M.TRUENAS_DB_VOLATILE, [])

    def test_the_dump_opens_the_database_read_only(self):
        a = self.db("a.db", {})
        os.chmod(a, 0o400)
        os.chmod(self.t, 0o500)
        try:
            self.dump_to = self.t.parent / f"{self.t.name}-out.txt"
            M.db_dump(str(a), str(self.dump_to))
            self.addCleanup(self.dump_to.unlink)
        finally:
            os.chmod(self.t, 0o700)
        self.assertEqual(sorted(os.listdir(self.t)), ["a.db"], "no journal or lock file beside it")

    def test_the_cli(self):
        a = self.db("a.db", {})
        out = self.t / "a.txt"
        r = subprocess.run([sys.executable, str(BS1 / "bs1-manifest"), "--db-dump", str(a), str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, str(BS1 / "bs1-manifest"), "--db-compare", str(out), str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(BASH, "needs bash")
class VerifyDatabase(unittest.TestCase):
    def setUp(self):
        self.t = t = Path(tempfile.mkdtemp(prefix="bs1-truenas-verify-"))
        self.addCleanup(shutil.rmtree, t, True)
        (t / "run" / "baseline").mkdir(parents=True)
        (t / "attempt").mkdir()
        self.lib = "\n".join([
            "set -uo pipefail",
            f"RUN={t}/run", f"ATTEMPT={t}/attempt", f"HERE={BS1}", f"TRUENAS_DB={t}/freenas-v1.db",
            functions(BS1 / "bs1-verify", "truenas_db_same"),
        ])
        Database.db(self, "freenas-v1.db", {})

    def same(self):
        return subprocess.run([BASH, "-c", f"{self.lib}\ntruenas_db_same; echo rc $?"], capture_output=True, text=True, timeout=60,
                              env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"})

    def test_the_restored_rows_pass(self):
        M.db_dump(str(self.t / "freenas-v1.db"), str(self.t / "run" / "baseline" / "truenas-db.txt"))
        r = self.same()
        self.assertIn("rc 0", r.stdout, r.stdout + r.stderr)
        self.assertIn("differing: 0", (self.t / "attempt" / "truenas-db-compare.txt").read_text())

    def test_a_lost_row_fails(self):
        M.db_dump(str(self.t / "freenas-v1.db"), str(self.t / "run" / "baseline" / "truenas-db.txt"))
        con = sqlite3.connect(self.t / "freenas-v1.db")
        con.execute("DELETE FROM services_services WHERE srv_service = 'ssh'")
        con.commit()
        con.close()
        r = self.same()
        self.assertIn("rc 1", r.stdout)
        self.assertIn("row services_services: 1 only before, 0 only after", r.stdout)

    def test_a_baseline_without_rows_fails(self):
        r = self.same()
        self.assertIn("rc 1", r.stdout)
        self.assertIn("the baseline has no truenas-db.txt", r.stdout)


if __name__ == "__main__":
    unittest.main()
