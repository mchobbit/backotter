"""A synthetic world for P2's tests (U2.2-U2.6): a fixture checkout (git), BKO_GIT's three
forks, both hosts' facts and a seeded ledger.  Nothing here touches a host or a default root.

    w = World(tmpdir)            the checkout: 42 SticTest checks, 50 HoltTest sources and 9
                                 variants, 13 gate cells, 52 guests, 7 images, with the real classes.json,
                                 estimates.tsv, drive.py, affected.sh and ram-sizes.tsv
    w.facts()                    {host: facts}, as facts_host.py gives them
    w.ctx(rows, facts=..., **options)   a plan.Ctx
    w.plan(text, rows, ...)      words.parse + plan.make
    w.seed(plan)                 PASS rows at each cell's current keys (one per distinct key)

All names, hashes and times are synthetic; the hosts' names are the only real ones.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEST2 = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))     # testvm/test2
WT = os.path.dirname(os.path.dirname(TEST2))
sys.path.insert(0, TEST2)

from t2lib import ledger, plan, words   # noqa: E402

HOSTS = ("chopin", "cinqcent")
OSES = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "omnios", "illumos", "haiku")
STIC_OS = {"freebsd15": "freebsd", "freebsd15-ufs": "freebsd", "hardenedbsd15": "freebsd", "ghostbsd26": "freebsd",
           "ghostbsd26-zfs": "freebsd", "freebsd14": "freebsd", "freebsd15-geli": "freebsd", "netbsd11": "netbsd",
           "alpinebsd": "netbsd", "openbsd8": "openbsd", "dragonfly6": "dragonfly", "omnios58": "omnios",
           "haiku6": "haiku"}
GUESTS = ("aerynos alpine324 alpinebsd arch artix-dinit artix-s6 cachyos debian12 debian13 debian13-stack debian13-zfs "
          "devuan6 dragonfly6 fedora44 fedora44-raid fedora44-uefi freebsd14 freebsd15 freebsd15-builder "
          "freebsd15-geli freebsd15-mirror freebsd15-ufs ghostbsd26 ghostbsd26-zfs haiku6 hardenedbsd15 mint22 mx25 "
          "netbsd11 nixos2605 omarchy omnios58 omnios58-mirror openbsd8 pop24 redox rocky10 rocky10-lvm "
          "rocky10-raid5 sculpt2610 tumbleweed ubuntu24 ubuntu26-raid ubuntu26-uefi ubuntu26-zfs ubuntu26-zfsmirror "
          "void-musl zorin18 endeavouros endeavouros-grub manjaro ubuntu26-luks chimera").split()
UEFI = set("aerynos alpinebsd debian13-stack fedora44-raid fedora44-uefi freebsd15-geli omarchy pop24 ubuntu26-raid "
           "ubuntu26-uefi ubuntu26-zfs endeavouros endeavouros-grub manjaro ubuntu26-luks kinoite44".split())
PLATS = words.PLATS
NEW_GUESTS = set("endeavouros endeavouros-grub manjaro ubuntu26-luks chimera fedora44-raid ubuntu26-raid rocky10-raid5 freebsd15-mirror "
                 "omnios58-mirror".split())
PM = {"apt": "debian12 debian13 debian13-stack debian13-zfs devuan6 mint22 mx25 pop24 ubuntu24 ubuntu26-raid ubuntu26-uefi "
             "ubuntu26-zfs ubuntu26-zfsmirror zorin18 ubuntu26-luks",
      "dnf": "fedora44 fedora44-raid fedora44-uefi rocky10 rocky10-lvm rocky10-raid5",
      "pacman": "arch artix-dinit artix-s6 cachyos omarchy endeavouros endeavouros-grub manjaro",
      "zypper": "tumbleweed", "xbps": "void-musl", "apk": "alpine324 chimera", "moss": "aerynos", "NixOS": "nixos2605",
      "FreeBSD": "freebsd14 freebsd15 freebsd15-geli freebsd15-mirror freebsd15-ufs ghostbsd26 ghostbsd26-zfs hardenedbsd15",
      "NetBSD": "netbsd11 alpinebsd", "OpenBSD": "openbsd8", "DragonFly": "dragonfly6", "SunOS": "omnios58 omnios58-mirror",
      "Haiku": "haiku6", "Redox": "redox", "Sculpt": "sculpt2610"}
PM_OF = {g: pm for pm, gs in PM.items() for g in gs.split()}
LINUX_PMS = ("apt", "dnf", "pacman", "zypper", "xbps", "apk", "moss")
BSD_PLAT = {"freebsd15": "freebsd", "freebsd15-mirror": "freebsd", "freebsd14": "freebsd", "ghostbsd26": "freebsd",
            "ghostbsd26-zfs": "freebsd", "hardenedbsd15": "freebsd", "netbsd11": "netbsd", "alpinebsd": "netbsd",
            "openbsd8": "openbsd", "dragonfly6": "dragonfly", "omnios58": "illumos", "omnios58-mirror": "illumos",
            "haiku6": "haiku"}
BSD_RESCUER = {"freebsd15": "freebsd15-ufs", "freebsd15-mirror": "freebsd15-ufs", "freebsd14": "freebsd15-ufs",
               "ghostbsd26": "freebsd15-ufs", "ghostbsd26-zfs": "freebsd15-ufs", "hardenedbsd15": "freebsd15-ufs",
               "netbsd11": "netbsd11", "alpinebsd": "netbsd11", "openbsd8": "openbsd8-newduid.qcow2",
               "dragonfly6": "dragonfly6-newfsid.qcow2", "omnios58": "omnios58-reguid.qcow2",
               "omnios58-mirror": "omnios58-reguid.qcow2", "haiku6": "haiku6"}
T0 = 1793491200        # 2026-11-01T00:00:00Z
ENV = {"GIT_AUTHOR_NAME": "t2", "GIT_AUTHOR_EMAIL": "t2", "GIT_COMMITTER_NAME": "t2", "GIT_COMMITTER_EMAIL": "t2",
       "GIT_AUTHOR_DATE": "2026-11-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-11-01T00:00:00Z",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def H(s):
    return hashlib.sha256(s.encode()).hexdigest()


def git(repo, *args, date=None):
    env = dict(os.environ, **ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def write(root, rel, text, mode=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    if mode:
        os.chmod(p, mode)


def stic_guests():
    return sorted(d for d in os.listdir(os.path.join(WT, "testvm", "stictest1"))
                  if os.path.isfile(os.path.join(WT, "testvm", "stictest1", d, "check.sh")))


def holt_kind(g):
    if g in BSD_PLAT:
        return "bsd"
    if g in ("redox", "sculpt2610"):
        return "offline"
    return "linux"


class World:
    def __init__(self, tmp):
        self.tmp = tmp
        self.repo = os.path.join(tmp, "repo")
        self.bko_git = os.path.join(tmp, "git")
        os.makedirs(self.repo)
        self.stic = stic_guests()
        self.forks = {}
        self.make_repo()
        self.make_forks()
        self.round_commits = {"rel4": dict(self.forks["c0"])}
        self._tick = 0

    # --- the checkout ---

    def make_repo(self):
        r = self.repo
        git(r, "init", "-q", "-b", "main")
        for rel in ("testvm/stictest1/drive.py", "testvm/stictest1/README.md", "testvm/compare/affected.sh",
                    "testvm/holttest1d/ram-sizes.tsv", "testvm/test2/classes.json", "testvm/test2/estimates.tsv"):
            os.makedirs(os.path.dirname(os.path.join(r, rel)), exist_ok=True)
            shutil.copy(os.path.join(WT, rel), os.path.join(r, rel))
        for g in self.stic:
            osd = STIC_OS.get(g, "linux")
            write(r, f"testvm/stictest1/{g}/check.sh",
                  f"#!/usr/bin/env bash\n# check.sh BIN_DIR PORT PREFIX: BIN_DIR is roundN/bin/{osd}\n"
                  f"slot_take 3\n~/backotter-work/cmp-run.sh --reserve 2 4 true\n", 0o755)
            write(r, f"testvm/stictest1/{g}/inner.sh", f"# {g}'s inner phase\n")
        write(r, "testvm/stictest1/freebsd15/extra.sh", "# shared by the FreeBSD ZFS checks\n")
        for rel in ("testvm/compare/verdict.py", "testvm/compare/fsmeta.py", "testvm/compare/guest-slot.sh",
                    "testvm/compare/rt-common.sh", "testvm/compare/expected-coverage.json", "testvm/vm-run",
                    "testvm/vm-lib.sh", "testvm/vm-mount", "testvm/vm-umount", "testvm/console-boot.py",
                    "testvm/holttest1d/ht1.sh", "testvm/holttest1d/ht1-bsd.sh", "testvm/holttest1d/ht1-offline.sh",
                    "testvm/holttest1d/ht1-lib.sh", "testvm/holttest1d/ht1-queue.sh", "testvm/holttest1d/ram-size.sh",
                    "testvm/holttest1d/judge.py", "testvm/audit/fork-tests.sh", "testvm/test2/gate/holt-test.sh",
                    "testvm/test2/gate/bincheck.sh", "testvm/test2/gate/loop.sh", "testvm/test2/gate/baseline.json",
                    "testvm/redox/console.py", "testvm/sculpt-check.sh"):
            write(r, rel, f"# fixture {rel}\n")
        write(r, "testvm/update/in-guest.sh", self.in_guest_text())
        for rel in ("testvm/vm-update", "testvm/compare/boot-state.sh", "testvm/make-fixture.sh", "testvm/test2/guest/job.sh",
                    "testvm/test2/guest/check-image.sh", "testvm/test2/guest/check-after.sh", "testvm/test2/guest/new-guest.sh",
                    "testvm/test2/guest/probe-redox.sh", "testvm/test2/guest/probe-sculpt.sh", "testvm/test2/t2lib/guest_judge.py",
                    "testvm/test2/img/boot.sh", "stick/boot-test.py", "testvm/holttest1d/img-boot.py"):
            write(r, rel, f"# fixture {rel}\n")
        for p_ in PLATS:
            write(r, f"stick/{p_}/checks.sh", f"# fixture {p_}\n")
            write(r, f"stick/{p_}/manifest.sh", f"# fixture {p_}\n")
            write(r, f"testvm/holttest1d/img-boot.d/{p_}.json", "{}\n")
        write(r, "testvm/test2/guest/known-lines.json", json.dumps({"all": ["x"], **{m: [m] for m in PM}}, indent=1, sort_keys=True))
        write(r, "testvm/test2/guest/limits.json", "{}\n")
        write(r, "testvm/test2/guest/spec.json", json.dumps(self.guest_spec(), indent=1, sort_keys=True))
        write(r, "testvm/test2/img/spec.json", json.dumps(self.img_spec(), indent=1, sort_keys=True))
        write(r, "holt/Cargo.toml", '[package]\nname = "bko-holt"\nversion = "0.2.4"\nedition = "2021"\n')
        write(r, "holt/Cargo.lock", 'version = 4\n\n[[package]]\nname = "bko-holt"\nversion = "0.2.4"\n')
        write(r, "holt/LICENSE", "fixture licence text\n")
        write(r, "holt/src/main.rs", "fn main() {}\n")
        write(r, "holt/src/load/commands.rs", "// prints the plan\n")
        write(r, "holt/src/load/exec.rs", "// runs the plan\n")
        for g in ("debian13", "mint22", "arch", "alpine324", "debian13-stack", "aerynos"):
            write(r, f"holt/tests/corpus/{g}/plan.txt", self.plan_text(g))
        for g in ("debian13-stack", "aerynos", "mint22"):
            write(r, f"holt/tests/corpus/{g}/plan+bigger.txt", self.plan_text(g + "+bigger"))
        write(r, "testvm/test2/stic/manifests.json", json.dumps(self.stic_spec(), indent=1, sort_keys=True))
        write(r, "testvm/test2/holt/spec.json", json.dumps(self.holt_spec(), indent=1, sort_keys=True))
        write(r, "testvm/test2/gate/layers.json", json.dumps(self.gate_spec(), indent=1, sort_keys=True))
        write(r, "testvm/test2/stic/flakes.json", json.dumps([{"guest": "ghostbsd26-zfs", "pattern": "ssh exit 255",
                                                                "why": "fixture", "ref": "fixture"}]))
        write(r, "testvm/test2/stic/limits.json", json.dumps({"debian12": {"selinux": "AppArmor keeps no labels"}}))
        git(r, "add", "-A")
        git(r, "commit", "-qm", "fixture world")

    @staticmethod
    def plan_text(name, change=None):
        st = {s: f"cmd {s} for {name}" for s in ("partition", "assemble", "format", "mount", "restore", "boot", "close")}
        if change:
            for s in change:
                st[s] += " (changed)"
        return "# plan of " + name + "\n" + "".join(f"# ---- {s} ----\n{v}\n" for s, v in st.items())

    def stic_spec(self):
        entries = {}
        for g in self.stic:
            osd = STIC_OS.get(g, "linux")
            e = {"bins": [osd] + (["linux/restic-bko"] if g == "haiku6" else []),
                 "harness": [f"testvm/stictest1/{g}/**", "testvm/compare/fsmeta.py"],
                 "judge": ["testvm/compare/verdict.py", f"entry:testvm/test2/stic/limits.json#{g}"]}
            if g in ("freebsd15-geli", "ghostbsd26-zfs"):
                e["harness"].append("testvm/stictest1/freebsd15/extra.sh")
            if g == "alpinebsd":
                e["xguest"] = ["netbsd11"]
            entries[g] = e
        return {"v": 1, "family": "stic", "entries": entries,
                "plumbing": ["testvm/vm-run", "testvm/vm-lib.sh", "testvm/compare/guest-slot.sh",
                             "testvm/console-boot.py", "testvm/stictest1/drive.py", "shared:cmp-run.sh",
                             "shared:testvm/compare/iolog.sh"]}

    @staticmethod
    def in_guest_text():
        out = ["#!/bin/sh\n", "# t2-section: prologue\n", "say hello\n", "# t2-section: Linux.common\n", "linux common\n"]
        for pm in LINUX_PMS:
            out += [f"# t2-section: Linux.{pm}\n", f"update with {pm}\n"]
        for osn in ("NixOS", "FreeBSD", "NetBSD", "OpenBSD", "DragonFly", "SunOS", "Haiku", "Redox"):
            out += [f"# t2-section: {osn}\n", f"update on {osn}\n"]
        out += ["# t2-section: prologue\n", "say done\n"]
        return "".join(out)

    def guest_spec(self):
        kinds = {"ssh": {"try": True,
                         "harness": ["testvm/vm-update", "testvm/update/in-guest.sh#prologue", "testvm/test2/guest/job.sh",
                                     "testvm/test2/guest/check-image.sh", "testvm/test2/guest/check-after.sh",
                                     "testvm/make-fixture.sh"],
                         "judge": ["testvm/test2/t2lib/guest_judge.py", "entry:testvm/test2/guest/known-lines.json#all",
                                   "entry:testvm/test2/guest/limits.json#{guest}",
                                   "entry:testvm/test2/guest/spec.json#entries.{guest}.checks",
                                   "entry:testvm/test2/classes.json#policy.guest.judge"],
                         "host": ["qemu", "ovmf"], "update": True},
                 "probe": {"harness": ["testvm/vm-update", "testvm/update/in-guest.sh#prologue", "testvm/test2/guest/job.sh",
                                       "testvm/test2/guest/check-image.sh", "testvm/test2/guest/check-after.sh"],
                           "judge": ["testvm/test2/t2lib/guest_judge.py", "entry:testvm/test2/guest/limits.json#{guest}"],
                           "host": ["qemu", "ovmf"]},
                 "redox": {"harness": ["testvm/test2/guest/job.sh", "testvm/test2/guest/probe-redox.sh", "testvm/redox/console.py"],
                           "judge": ["testvm/test2/t2lib/guest_judge.py"], "host": ["qemu", "ovmf"], "update": False},
                 "sculpt": {"harness": ["testvm/test2/guest/job.sh", "testvm/test2/guest/probe-sculpt.sh",
                                        "testvm/sculpt-check.sh"],
                            "judge": ["testvm/test2/t2lib/guest_judge.py"], "host": ["qemu", "ovmf"], "update": False}}
        entries = {}
        for g in GUESTS:
            if g == "freebsd15-builder":
                continue
            pm = PM_OF[g]
            e = {"kind": "ssh", "plat": BSD_PLAT.get(g, "linux"), "checks": {"pm": pm, "fw": "uefi" if g in UEFI else "bios",
                                                                              "established": g not in NEW_GUESTS}}
            if pm in LINUX_PMS:
                e["harness"] = ["testvm/update/in-guest.sh#Linux.common", f"testvm/update/in-guest.sh#Linux.{pm}",
                                "testvm/compare/boot-state.sh"]
            elif pm not in ("Redox", "Sculpt"):
                e["harness"] = [f"testvm/update/in-guest.sh#{pm}"]
            if g in NEW_GUESTS:
                e.setdefault("harness", []).append("testvm/test2/guest/new-guest.sh")
            if g == "haiku6":
                e["kind"], e["update"] = "probe", "name-only"
            if pm == "Redox":
                e["kind"] = "redox"
            if pm == "Sculpt":
                e["kind"] = "sculpt"
            entries[g] = e
        return {"v": 1, "family": "guest", "kinds": kinds, "entries": entries,
                "excluded": {"freebsd15-builder": "a build helper"},
                "plumbing": ["testvm/vm-run", "testvm/vm-lib.sh", "shared:cmp-run.sh", "shared:testvm/compare/iolog.sh"]}

    @staticmethod
    def img_spec():
        entries = {}
        for p_ in PLATS:
            entries[p_] = {"plat": p_, "bins_from_image": True,
                           "bins": ([] if p_ == "haiku" else ["PLAT/restic-bko"]) +
                                   ["PLAT/rustic-bko", "PLAT/bko-holt", "PLAT/test-image", "PLAT/release-image"],
                           "harness": ["stick/boot-test.py", "stick/PLAT/checks.sh", "stick/PLAT/manifest.sh",
                                       "?stick/PLAT/config-checks.sh", "testvm/test2/img/boot.sh"]}
        return {"v": 1, "family": "img", "entries": entries,
                "plumbing": ["shared:cmp-run.sh", "shared:testvm/compare/iolog.sh"]}

    def holt_spec(self):
        var = lambda replace: {"replace": replace, "bins_from_image": True, "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"],
                               "rescuer": ["@image"],
                               "harness": ["testvm/holttest1d/img-boot.py", "testvm/holttest1d/img-boot.d/{plat}.json",
                                           "stick/boot-test.py"]}
        kinds = {"linux": {"plat": "linux", "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"],
                           "harness": ["testvm/holttest1d/ht1.sh", "testvm/vm-mount", "testvm/vm-umount",
                                       "testvm/compare/fsmeta.py"],
                           "judge": ["testvm/holttest1d/judge.py"], "rescuer": ["@firmware", "bin/*.deb", "bin/bcachefs"],
                           "variants": {"img": var(["rescuer", "bins"])}},
                 "bsd": {"bins": ["PLAT/bko-holt", "PLAT/rustic-bko"],
                         "harness": ["testvm/holttest1d/ht1.sh", "testvm/holttest1d/ht1-bsd.sh"],
                         "judge": ["testvm/holttest1d/judge.py"], "variants": {"img": var(["bins"])}},
                 "offline": {"plat": "linux", "bins": ["PLAT/bko-holt", "PLAT/rustic-bko"],
                             "harness": ["testvm/holttest1d/ht1.sh", "testvm/holttest1d/ht1-offline.sh"],
                             "judge": ["testvm/holttest1d/judge.py"], "rescuer": ["rescuer.qcow2"]}}
        entries = {}
        for g in GUESTS:
            if g in ("freebsd15-builder", "freebsd15-ufs", "freebsd15-geli"):
                continue
            k = holt_kind(g)
            e = {"kind": k}
            if g in UEFI:
                e["firmware"] = "uefi"
            if k == "bsd":
                e["plat"] = BSD_PLAT[g]
                e["rescuer"] = [BSD_RESCUER[g]]
            if g == "redox":
                e["harness"] = ["testvm/redox/console.py"]
            if g == "sculpt2610":
                e["harness"] = ["testvm/sculpt-check.sh"]
            entries[g] = e
        return {"v": 1, "family": "holt", "kinds": kinds, "entries": entries,
                "plumbing": ["testvm/holttest1d/ht1-lib.sh", "testvm/holttest1d/ht1-queue.sh",
                             "testvm/holttest1d/ram-size.sh", "testvm/holttest1d/ram-sizes.tsv", "testvm/vm-run",
                             "shared:cmp-run.sh", "shared:testvm/compare/iolog.sh"]}

    def gate_spec(self):
        e = {"holt": {"part": "fast", "pin": "chopin", "harness": ["holt/**", "testvm/test2/gate/holt-test.sh"],
                      "host": ["rustc"]},
             "fork:restic": {"part": "fast", "pin": "chopin", "harness": ["testvm/audit/fork-tests.sh"],
                             "gitrepos": ["restic"]},
             "fork:rustic_core": {"part": "fast", "pin": "cinqcent", "harness": ["testvm/audit/fork-tests.sh"],
                                  "gitrepos": ["rustic_core"]},
             "fork:rustic": {"part": "fast", "pin": "cinqcent", "harness": ["testvm/audit/fork-tests.sh"],
                             "gitrepos": ["rustic"]},
             "fork-root:restic": {"part": "fast", "pin": "chopin", "harness": ["testvm/audit/fork-tests.sh"],
                                  "gitrepos": ["restic"]},
             "bin:abi": {"part": "fast", "pin": "chopin", "bins": ["freebsd"], "harness": ["testvm/test2/gate/bincheck.sh"]},
             "bin:static": {"part": "fast", "pin": "chopin", "bins": ["linux"], "harness": ["testvm/test2/gate/bincheck.sh"]},
             "bin:version": {"part": "fast", "pin": "chopin", "bins": ["linux", "freebsd"],
                             "harness": ["testvm/test2/gate/bincheck.sh"]},
             "loop:linux": {"part": "slow", "per_host": True, "bins": ["linux"],
                            "harness": ["testvm/test2/gate/loop.sh", "testvm/compare/rt-common.sh"],
                            "judge": ["testvm/compare/verdict.py", "testvm/compare/expected-coverage.json"],
                            "host": ["kernel"]},
             "loop:sparse": {"part": "slow", "per_host": True, "bins": ["linux"],
                             "harness": ["testvm/test2/gate/loop.sh", "testvm/compare/rt-common.sh"],
                             "judge": ["testvm/compare/verdict.py"], "host": ["kernel"]},
             "selftest": {"part": "fast", "pin": "mac", "harness": ["testvm/test2/classes.json"]}}
        return {"v": 1, "family": "gate", "entries": e}

    # --- BKO_GIT ---

    def make_forks(self):
        os.makedirs(self.bko_git)
        files = {
            "restic": {"internal/global/global.go": 'package global\n\nconst Version = "0.19.1+backotter.5"\n',
                       "internal/fs/attrs_freebsd.go": "//go:build freebsd\n\npackage fs\n\nvar x = 1\n",
                       "internal/restorer/restorer.go": "package restorer\n\nvar y = 1\n",
                       "BACKOTTER.md": "notes\n"},
            "rustic_core": {"crates/core/Cargo.toml": '[package]\nname = "rustic_core"\nversion = "0.13.0+backotter.6"\n',
                            "Cargo.lock": 'version = 4\n\n[[package]]\nname = "rustic_core"\nversion = "0.13.0+backotter.6"\n',
                            "crates/core/src/backend/osattrs/freebsd.rs": "// freebsd attrs\n",
                            "crates/core/src/lib.rs": "// lib\n"},
            "rustic": {"Cargo.toml": '[package]\nname = "rustic-rs"\nversion = "0.11.4+backotter.7"\n',
                       "Cargo.lock": 'version = 4\n\n[[package]]\nname = "rustic-rs"\nversion = "0.11.4+backotter.7"\n',
                       "src/main.rs": "fn main() {}\n"},
        }
        c = {}
        for repo, fs in files.items():
            d = os.path.join(self.bko_git, repo)
            os.makedirs(d)
            git(d, "init", "-q", "-b", "backotter")
            for rel, text in fs.items():
                write(d, rel, text)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "c0", date="2026-11-01T00:00:00Z")
            c.setdefault("c0", {})[repo] = git(d, "rev-parse", "HEAD")
            # c1: a version bump only
            for rel in fs:
                if rel.endswith(("Cargo.toml", "Cargo.lock", "global.go")):
                    p = os.path.join(d, rel)
                    with open(p) as fh:
                        t = fh.read().replace("backotter.5", "backotter.X").replace("backotter.6", "backotter.Y") \
                            .replace("backotter.7", "backotter.Z")
                    with open(p, "w") as fh:
                        fh.write(t)
            git(d, "commit", "-qam", "c1 version", date="2026-11-02T00:00:00Z")
            c.setdefault("c1", {})[repo] = git(d, "rev-parse", "HEAD")
            # c2: FreeBSD-only code (restic, rustic_core); rustic: its Cargo.lock takes rustic_core c2
            if repo == "restic":
                write(d, "internal/fs/attrs_freebsd.go", "//go:build freebsd\n\npackage fs\n\nvar x = 2\n")
            elif repo == "rustic_core":
                write(d, "crates/core/src/backend/osattrs/freebsd.rs", "// freebsd attrs, changed\n")
            else:
                write(d, "src/main.rs", "fn main() {}\n")   # unchanged
            git(d, "commit", "-qam", "c2 freebsd", "--allow-empty", date="2026-11-03T00:00:00Z")
            c.setdefault("c2", {})[repo] = git(d, "rev-parse", "HEAD")
            # c3: code every OS builds
            if repo == "restic":
                write(d, "internal/restorer/restorer.go", "package restorer\n\nvar y = 3\n")
            elif repo == "rustic_core":
                write(d, "crates/core/src/lib.rs", "// lib, changed\n")
            else:
                write(d, "src/main.rs", "fn main() { /* changed */ }\n")
            git(d, "commit", "-qam", "c3 all", date="2026-11-04T00:00:00Z")
            c.setdefault("c3", {})[repo] = git(d, "rev-parse", "HEAD")
            git(d, "checkout", "-q", c["c0"][repo])
            git(d, "branch", "-q", "-f", "backotter", c["c0"][repo])
            git(d, "checkout", "-q", "backotter")
        self.forks = c

    def set_heads(self, name):
        for repo, h in self.forks[name].items():
            d = os.path.join(self.bko_git, repo)
            git(d, "checkout", "-q", h)
            git(d, "branch", "-q", "-f", "backotter", h)
            git(d, "checkout", "-q", "backotter")

    # --- facts ---

    def binset(self, tag, commits, digest_extra=None, only=None):
        sums, cm, dg, dt = {}, {}, {}, {}
        for o in OSES:
            for n in ("restic-bko", "rustic-bko"):
                if o == "haiku" and n == "restic-bko":
                    continue
                name = f"{o}/{n}"
                t = (only or {}).get(name, tag)
                sums[name] = H(f"bin {t} {name}")
                dg[name] = H(f"digest {t} {name}")
                extra = (digest_extra or {}).get(name, "")
                dt[name] = (f"{n} {o}\nsources {H('src ' + t)}\nprofile release\n{extra}"
                            f"digest {dg[name]}\n")
            cm[f"{o}/restic-bko"] = commits["restic"]
            cm[f"{o}/rustic-bko"] = {"commit": commits["rustic"], "rustic_core": commits["rustic_core"]}
        return {"path": f"/home/m/backotter-work/{tag}/bin", "sums": sums, "commits": cm, "digests": dg,
                "digest_texts": dt, "build_info": f"build {tag}"}

    def images(self, tag="i1", commit=None, only=None, pin_current=True):
        """facts.images: every platform (ONLY: those), the bko-holt commit COMMIT (default HEAD; "": none)."""
        out = {}
        for p_ in (only or PLATS):
            names = ["rustic-bko", "bko-holt"] + ([] if p_ == "haiku" else ["restic-bko"])
            c = self.head() if commit is None else (commit or None)
            out[p_] = {"bins": {n: H(f"{tag} {p_} {n}") for n in names},
                       "release": {"name": f"{p_}.img", "sha256": H(f"release {tag} {p_}")},
                       "test": {"name": f"{p_}.img", "path": f"/x/{p_}.img", "size": 1, "mtime_ns": 1, "inode": 1},
                       "pin": {"sha256": H(f"pin {tag} {p_}"), "current": pin_current, "bins_stale": False},
                       "done_exit": 0, "holt_commit": c, "bko_bin_info": "fixture"}
        return out

    def fact_guests(self):
        """The guests the hosts' facts name: GUESTS, and a SticTest check's guest that isn't one
        yet (kinoite44 until its Test2 registration, docs/design/bazzite.md B7)."""
        return GUESTS + [g for g in self.stic if g not in GUESTS]

    @staticmethod
    def fp(gen, holt="h1", stic="s1", fine="f1", holt_was=(), stic_was=(), rules="r"):
        """A guest's fingerprint record, as facts_host.py gives it, for generation number GEN of the guest."""
        return {"holt": H("holt " + holt), "stic": H("stic " + stic), "fine": H("fine " + fine),
                "holt_was": [H("holt " + x) for x in holt_was], "stic_was": [H("stic " + x) for x in stic_was],
                "rules": rules, "gen_of": gen}

    def facts(self, kernel="6.12.115", gens=None, plumb="p1", rescuer="r1", rustic_tag="bko", round_tag="rel4",
              round_commits=None, sets_extra=None, uefi_rescuer=("chopin", "cinqcent"), hosts=HOSTS, images=None,
              fps=None, qemu=None):
        """FPS: {guest: fp(...)}: its fingerprint record, generation filled in.  QEMU: {host: version}."""
        out = {}
        rc = round_commits or self.round_commits["rel4"]
        for h in hosts:
            f = {"v": 1, "host": h, "t": "2026-11-08T05:00:00Z",
                 "versions": {"kernel": kernel, "zfs": "2.3.9", "qemu": (qemu or {}).get(h, "QEMU 10.0.2"), "ovmf": "2025.02-8",
                              "pkgs": {}, "rustc": "rustc 1.94.1"},
                 "preflight": {"tctl_c": 50.0, "ram": {"mounted": True, "free": 30 << 30}, "kvm": True,
                               "scratch_free_gb": 900.0, "pswpout": 0, "diskstats": {}},
                 "sets": {"bko-bin": self.binset(rustic_tag, self.forks["c0"]),
                          "roundrel4": self.binset(round_tag, rc)},
                 "rounds": {"rel4": {"path": "/home/m/backotter-work/roundrel4/bin", "restic": rc["restic"],
                                     "rustic": rc["rustic"], "rustic_core": rc["rustic_core"], "sums": True}},
                 "holt_bins": {},
                 "guests": {g: {"generation": H(f"gen {g} {(gens or {}).get(g, 1)}"), "updates": 1,
                                "files": {"vm-run-options": H("opts " + g)},
                                "firmware": "uefi" if g in UEFI else "bios"} for g in self.fact_guests()},
                 "rescuers": {"images": {"rescuer.qcow2": {"sha256": H("rescuer " + rescuer)},
                                         "openbsd8-newduid.qcow2": {"sha256": H("ob")},
                                         "dragonfly6-newfsid.qcow2": {"sha256": H("df")},
                                         "omnios58-reguid.qcow2": {"sha256": H("om")}},
                              "bin": {"f2fs-tools_1.16.0-1_amd64.deb": H("f2fs"), "bcachefs": H("bcachefs")}},
                 "plumbing": {"shared:cmp-run.sh": H("cmp " + plumb), "shared:testvm/compare/iolog.sh": H("iolog")},
                 "host_inputs": {}, "synced": {}, "copies": []}
            if h in uefi_rescuer:
                f["rescuers"]["images"]["rescuer-uefi.qcow2"] = {"sha256": H("rescuer-uefi " + rescuer)}
            for k, v in (sets_extra or {}).items():
                f["sets"][k] = v
            f["images"] = images if images is not None else self.images()
            for g, rec in (fps or {}).items():
                f["guests"][g]["fingerprint"] = dict(rec, generation=H(f"gen {g} {rec['gen_of']}"))
            out[h] = f
        return out

    # --- plans and rows ---

    def ctx(self, rows=(), facts=None, hosts=HOSTS, **options):
        opts = dict(bko_git=self.bko_git, now=T0 + 8 * 86400)
        opts.update(options)
        return plan.Ctx(self.repo, hosts=list(hosts), facts=facts if facts is not None else self.facts(),
                        ledger=list(rows), copy_preview={"id": "fixture", "dirty": {}, "commit": None}, options=opts)

    def word(self, text, ctx=None):
        return words.parse(text.split(), plan.known_names(ctx or self.ctx()))

    def plan(self, text, rows=(), facts=None, hosts=HOSTS, **options):
        c = self.ctx(rows, facts, hosts, **options)
        self.last_facts = c.facts
        return plan.make(words.parse(text.split(), plan.known_names(c)), c)

    def row(self, pc, host, verdict="PASS", kind="run", age_h=0.0, needs=None, klass=None, inputs=None,
            committed=True, dirty=None, rule=None, extra=None, meta=None, via=None, attempt=1, valid=True, plan_=None):
        """A complete row for PlanCell PC at HOST's current inputs (or INPUTS)."""
        self._tick += 1
        res = pc.res[host]
        inp = copy.deepcopy(inputs if inputs is not None else res.inputs)
        t = T0 + 7 * 86400 - int(age_h * 3600) + self._tick
        stamp = lambda x: __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(x))
        idst = __import__("time").strftime("%Y%m%dT%H%M%SZ", __import__("time").gmtime(t))
        cls = klass or {"PASS": "pass", "FAIL": "fail", "FLAKY-FAIL": "fail", "HARNESS": "harness", "LOST": "lost"}[verdict]
        r = {"id": f"{host}-{idst}-{self._tick:08x}", "t": stamp(t), "start": stamp(t - 100) if kind == "run" else None,
             "host": host, "origin": f"fixture:{self._tick}:{pc.cell}", "family": pc.family, "cell": pc.cell,
             "kind": kind, "run": "fixture", "word": "fixture", "rule": rule or "fixture", "reasons": [],
             "attempt": attempt, "inputs": inp, "spec": None, "planned_key": None, "valid": valid,
             "verdict": verdict, "class": cls, "needs": needs if needs is not None else {"seeded": "PASS"},
             "counts": {"PASS": 1}, "gm": 1.0, "wall_s": 100, "waits_s": 0, "wear": {},
             "commit": self.head(), "committed": committed, "dirty": dirty,
             "copy": "fixture", "meta": meta if meta is not None else self.meta_for(pc, host, plan_),
             "extra": extra or {}, "via": via}
        return ledger.complete(r, host=host)

    def meta_for(self, pc, host, p=None):
        if pc.family == "holt":
            return {"holt": {"code": None, "commit": self.head()}}
        if host not in HOSTS:
            return {}
        sets = (getattr(self, "last_facts", None) or self.facts())[host]["sets"]
        name = (p.set or {}).get("name") if p is not None else "roundrel4"
        s = sets.get(name or "roundrel4") or sets["roundrel4"]
        return {"bin": {"set": name, "commits": s["commits"], "digest_texts": s["digest_texts"]}}

    def seed(self, p, hosts=None, age=None, **kw):
        """PASS rows for every cell of plan P at its current keys (one per distinct key)."""
        out = []
        for pc in p.cells:
            seen = set()
            for h in sorted(pc.res):
                if hosts and h not in hosts:
                    continue
                if pc.keys[h] in seen:
                    continue
                seen.add(pc.keys[h])
                out.append(self.row(pc, h, age_h=(age or {}).get(pc.cell, 24.0), plan_=p, **kw))
        return out

    def edit(self, rel, text, commit=False):
        write(self.repo, rel, text)
        if commit:
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-qm", f"edit {rel}", date="2026-11-07T00:00:00Z")
            self._head = None

    def reset(self, commit):
        git(self.repo, "reset", "-q", "--hard", commit)
        git(self.repo, "clean", "-qfd")
        self._head = None

    def head(self):
        if getattr(self, "_head", None) is None:
            self._head = git(self.repo, "rev-parse", "HEAD")
        return self._head

    def read(self, rel):
        with open(os.path.join(self.repo, rel)) as f:
            return f.read()
