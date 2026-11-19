"""S-apk over apk-tools 3's repository files (apk-repositories(5)): /etc/apk/repositories, then
/etc/apk/repositories.d/*.list over /lib/apk/repositories.d/*.list (Chimera's
/usr/lib/apk/repositories.d, read through its /lib -> usr/lib).  The judge (no host, no VM) and
check-image.sh's S-apk line, run under sh against a scratch tree."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from t2lib import guest_judge as J  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

# Chimera's ROOTFS-20261103-full tarball, ./usr/lib/apk/repositories.d/01-repo-main.list (156 bytes).
CHIMERA_MAIN = ("set -default CHIMERA_REPO_URL=https://repo.chimera-linux.org\n"
                "set -default CHIMERA_REPO_RELEASE=current\n\n"
                "v3 ${CHIMERA_REPO_URL}/${CHIMERA_REPO_RELEASE}/main")
CHIMERA_OSR = ("file /etc/os-release", 'NAME="Chimera"\nID="chimera"\nPRETTY_NAME="Chimera Linux"')
ALPINE_OSR = ("file /etc/os-release", "ID=alpine\nVERSION_ID=3.24.1")


def sec(*parts):
    out = []
    for head, body in parts:
        out += [f"=== {head}", body]
    out.append(J.END)
    return J.parse_sections("\n".join(out) + "\n")


def src(s):
    return J.judge_image(s, {"pm": "apk", "fw": "bios", "established": True,
                             "rules": {"source": "S-apk", "loader": "L-grub", "kernel": "K-deb",
                                       "boot": "B-lin", "mounts": "M-lin"}}, updates=1)["image:sources"]


class TestJudge(unittest.TestCase):
    def test_alpine_unchanged(self):
        # test_guest_judge.py's three cases
        self.assertEqual(src(sec(ALPINE_OSR, ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.24/main"))).v, "PASS")
        self.assertEqual(src(sec(ALPINE_OSR, ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.22/main"))).v, "FAIL")
        self.assertEqual(src(sec(("file /etc/apk/repositories", "#nothing"))).v, "FAIL")
        # a tagged edge repository still counts as its branch's, as before
        tagged = "https://dl-cdn.alpinelinux.org/alpine/v3.22/main\n@testing https://dl-cdn.alpinelinux.org/alpine/edge/testing"
        self.assertEqual(src(sec(ALPINE_OSR, ("file /etc/apk/repositories", tagged))).v, "PASS")
        self.assertEqual(J.s_apk(sec(ALPINE_OSR, ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.22/main"))),
                         ["no apk repository of its branch"])

    def test_chimera_lib_list(self):
        # check-image.sh emits Chimera's /usr/lib list by apk's own path, /lib (-> usr/lib)
        s = sec(CHIMERA_OSR, ("file /lib/apk/repositories.d/01-repo-main.list", CHIMERA_MAIN))
        self.assertEqual(J.apk_repositories(s), ["https://repo.chimera-linux.org/current/main"])
        self.assertEqual(src(s).v, "PASS")

    def test_usr_lib_not_read(self):
        # apk never reads /usr/lib/apk/repositories.d by that name: on a root that isn't usr-merged
        # a stray list there neither makes a repository nor rescues a stale Alpine branch
        stray = ("file /usr/lib/apk/repositories.d/x.list", "https://dl-cdn.alpinelinux.org/alpine/v3.24/main")
        self.assertEqual(J.apk_repositories(sec(CHIMERA_OSR, stray)), [])
        self.assertEqual(src(sec(CHIMERA_OSR, stray)).v, "FAIL")
        stale = ("file /etc/apk/repositories", "https://dl-cdn.alpinelinux.org/alpine/v3.22/main")
        self.assertEqual(J.s_apk(sec(ALPINE_OSR, stale, stray)), ["no apk repository of its branch"])

    def test_no_files(self):
        self.assertEqual(J.s_apk(sec(CHIMERA_OSR)), ["no enabled apk repository"])
        self.assertEqual(src(sec(CHIMERA_OSR)).v, "FAIL")
        # only variables and comments: no repository
        only_set = "# mirror\nset -default CHIMERA_REPO_URL=https://repo.chimera-linux.org"
        self.assertEqual(src(sec(CHIMERA_OSR, ("file /lib/apk/repositories.d/01-repo-main.list", only_set))).v, "FAIL")

    def test_etc_overrides_lib(self):
        lib = ("file /lib/apk/repositories.d/01-repo-main.list", CHIMERA_MAIN)
        # a same-named file in /etc hides /lib's: here it disables the repository
        off = sec(CHIMERA_OSR, lib, ("file /etc/apk/repositories.d/01-repo-main.list", "# v3 https://repo.chimera-linux.org/current/main"))
        self.assertEqual(J.apk_repositories(off), [])
        self.assertEqual(src(off).v, "FAIL")
        # ... or points it at a mirror
        mirror = sec(CHIMERA_OSR, lib, ("file /etc/apk/repositories.d/01-repo-main.list", "v3 https://mirror.example/chimera/current/main"))
        self.assertEqual(J.apk_repositories(mirror), ["https://mirror.example/chimera/current/main"])
        # a variable set in an earlier-named /etc file wins over /lib's set -default (the man page's example)
        var = sec(CHIMERA_OSR, lib, ("file /etc/apk/repositories.d/00-vars.list", "set CHIMERA_REPO_URL=https://mirror.example/chimera"))
        self.assertEqual(J.apk_repositories(var), ["https://mirror.example/chimera/current/main"])
        # another name adds to it (the user repository)
        user = sec(CHIMERA_OSR, lib, ("file /lib/apk/repositories.d/02-repo-user.list",
                                      "v3 ${CHIMERA_REPO_URL}/${CHIMERA_REPO_RELEASE}/user"))
        self.assertEqual(J.apk_repositories(user), ["https://repo.chimera-linux.org/current/main",
                                                    "https://repo.chimera-linux.org/current/user"])

    def test_line_forms(self):
        s = sec(("file /etc/apk/repositories.d/a.list", "v3 @t https://x.example/r main community\n"
                 "ndx https://y.example/idx.adb\n${UNSET}/z\n  # indented comment"))
        self.assertEqual(J.apk_repositories(s), ["https://x.example/r/main", "https://x.example/r/community",
                                                 "https://y.example/idx.adb", "${UNSET}/z"])
        # a file not named *.list is not read
        self.assertEqual(J.apk_repositories(sec(("file /etc/apk/repositories.d/a.conf", "https://x.example/r"))), [])


class TestCheckImage(unittest.TestCase):
    """check-image.sh's S-apk block with its paths moved into a scratch root."""

    def block(self, root):
        with open(os.path.join(REPO, "testvm/test2/guest/check-image.sh"), encoding="utf-8") as f:
            text = f.read()
        start = text.index("has_rule S-apk && ")
        body = text[start:text.index("\n", start) + 1]
        for d in ("/etc/apk", "/usr/lib/apk", "/lib/apk"):
            body = body.replace(" " + d + "/", f" {root}{d}/")
        self.assertNotIn(" /etc/apk", body)
        self.assertNotIn(" /lib/apk", body)
        self.assertNotIn(" /usr/lib/apk", body)
        head = text[:text.index("\ncmd image-rules")]
        code = head + '\nRULES="S-apk"\n' + body
        out = subprocess.run([os.environ.get("T2_SH", "sh"), "-c", code], capture_output=True, text=True, check=True).stdout
        return [x[len("=== file " + root):] for x in out.splitlines() if x.startswith("=== file ")], out

    def tree(self, merged=True):
        root = tempfile.mkdtemp(prefix="t2-apk-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "usr/lib/apk/repositories.d"))
        os.makedirs(os.path.join(root, "etc/apk"))
        if merged:
            os.symlink("usr/lib", os.path.join(root, "lib"))
        else:
            os.makedirs(os.path.join(root, "lib/apk/repositories.d"))
        with open(os.path.join(root, "usr/lib/apk/repositories.d/01-repo-main.list"), "w") as f:
            f.write(CHIMERA_MAIN + "\n")
        return root

    def test_usr_merged(self):
        # Chimera: /lib -> usr/lib, the list emitted once, by its /lib name
        root = self.tree()
        files, out = self.block(root)
        self.assertEqual(files, ["/lib/apk/repositories.d/01-repo-main.list"])
        self.assertEqual(J.apk_repositories(J.parse_sections(out.replace(root, "") + J.END + "\n")),
                         ["https://repo.chimera-linux.org/current/main"])

    def test_etc_and_split_lib(self):
        root = self.tree(merged=False)
        os.makedirs(os.path.join(root, "etc/apk/repositories.d"))
        for p in ("etc/apk/repositories", "etc/apk/repositories.d/00-vars.list", "lib/apk/repositories.d/9.list"):
            with open(os.path.join(root, p), "w") as f:
                f.write("# x\n")
        files, _ = self.block(root)
        # /usr/lib's 01-repo-main.list is not apk's on a split root, so it is not emitted
        self.assertEqual(files, ["/etc/apk/repositories", "/etc/apk/repositories.d/00-vars.list",
                                 "/lib/apk/repositories.d/9.list"])

    def test_nothing(self):
        root = tempfile.mkdtemp(prefix="t2-apk-")
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(self.block(root)[0], [])


if __name__ == "__main__":
    unittest.main()
