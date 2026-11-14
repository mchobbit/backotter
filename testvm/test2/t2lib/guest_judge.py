"""GuestTest2's judge (contract 1.3, 2.1): what a guest's hook output, logs and rules mean.
No I/O: every function takes text and dicts, so the judge is a key input and unit-testable.

    parse_sections(text)           a hook's output as Sections (=== file PATH, === cmd NAME, === end)
    check_hook(text, required)     (ok, why): empty, cut off, or a section missing
    required_image(checks), required_after(checks)
    judge_image(sec, checks, policy, updates)    {need: Need}: step 2, S-*, L-*, K-*, free space, #20
    after_lines(sec, checks, steps)              {need: [line] or None (SKIP)}, step 3's readings
    judge_after(before, after, checks, classed, steps)   {need: Need}: standing lines LIMIT, new ones FAIL
    merge_gate(before, after, checks, classed, hooks_ok, hook_why, steps)   (ok, why, new lines)
    new_guest_needs(sec, checks), probe_need(sec)    step 4
    normalize(line), known_filter(known, pm), sweep_lines(...)    step 5, the log sweep
    apply_limits(needs, standing, classed)       LIMIT or LIMIT-NEW, line by line
    verdict_of(needs, harness_why)               (verdict, class, to_class_only)
    match_flake(guest, tail, flakes)             only vm-update's failure tail
    grade(...)                                   step 6
"""

import fnmatch
import re

END = "=== end"
HEAD = re.compile(r"=== (file|cmd) (.+)\Z")
IMAGE_NEEDS = ("image:sources", "image:loader", "image:zfs-cache", "image:free", "image:kernels")
AFTER_NEEDS = ("after:boot-state", "after:failed-units", "after:fstab", "after:shadow", "after:newest-kernel")
NEW_NEEDS = ("new:firmware", "new:ssh", "new:restic", "new:rustic", "new:smoke")
KERNELS_MAX = {"K-deb": 3, "K-fed": 5, "K-ostree": 2, "K-pac": 2, "K-apk": 3, "K-void": 2, "K-moss": 3, "K-nix": 3, "K-fbsd": 3,
               "K-nbsd": 3, "K-obsd": 4, "K-dfly": 3, "K-ill": 4, "K-haiku": 2, "K-alpbsd": 3,
               "K-gentoo": 3, "K-anduinos": 3, "K-pika": 3}
NO_RULE = "no rule for it"
# K-abroot (Vanilla OS, vanillaos.md V5): check-image.sh lists "LABEL vmlinuz-K" for each kernel
# in the init LV's LABEL/ (one directory a root), "LABEL none" for a root with none.  Both roots
# boot, so each needs one; an upgrade writes the other root's afresh, so a root holds a few at most.
ABROOT_KERNELS_PER_ROOT = 2
ABROOT_ROOTS = "the init LV holds no directory for one of the two roots"
ABROOT_NOT_DEFAULT = "the booted root isn't the one the master grub.cfg's default boots"
# K-anduinos: check-image.sh marks a /boot/vmlinuz-K without dracut's /boot/initrd.img-K (the
# name Debian's dracut.conf.d gives it, which GRUB's 10_linux looks for).
NO_INITRD_MARK = "no-initrd"
NO_INITRD = "a kernel without its dracut image /boot/initrd.img-K"
# K-pika (PikaOS, pikaos.md): Debian's /boot/vmlinuz-K (kernel-pika), each with booster's
# /boot/booster.img-K; check-image.sh marks one without it.  The update keeps the newest two.
NO_BOOSTER_MARK = "no-booster"
NO_BOOSTER = "a kernel without its booster image /boot/booster.img-K"
U0 = "not updated yet (u0): frozen by its builder"


class Need:
    """One need's result: V (PASS FAIL LIMIT SKIP), WHY (a SKIP's reason, a PASS's note), LINES
    (what failed, one finding a line, stable between runs: limits.json classes by line)."""

    def __init__(self, v, why=None, lines=None, standing=None, new=None):
        self.v, self.why, self.lines = v, why, list(lines or [])
        self.standing, self.new = list(standing or []), list(new or [])

    def __repr__(self):
        return f"Need({self.v}, {self.why!r}, {self.lines!r})"


def skip(why):
    return Need("SKIP", why)


def verdict_lines(lines, note=None):
    return Need("FAIL", None, lines) if lines else Need("PASS", note)


# --- hook output ---------------------------------------------------------------------------

class Sections:
    """A hook's output.  .ended: it reached === end.  .file(PATH) the lines of a file that
    was there, or None; .glob(PATTERN) {path: lines}; .cmd(NAME) a command's lines, or None."""

    def __init__(self):
        self.files, self.cmds = {}, {}
        self.ended, self.empty = False, True

    def file(self, path):
        return self.files.get(path)

    def text(self, path):
        x = self.files.get(path)
        return None if x is None else "\n".join(x)

    def glob(self, pattern):
        return {p: v for p, v in self.files.items() if fnmatch.fnmatchcase(p, pattern)}

    def first(self, *paths):
        for p in paths:
            if p in self.files:
                return p
        return None

    def cmd(self, name):
        return self.cmds.get(name)


def parse_sections(text):
    s = Sections()
    cur = None
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        if line.strip():
            s.empty = False
        if line == END:
            s.ended = True
            cur = None
            continue
        m = HEAD.match(line)
        if m:
            s.ended = False   # output after an === end isn't the end
            cur = []
            (s.files if m.group(1) == "file" else s.cmds)[m.group(2)] = cur
            continue
        if cur is not None:
            cur.append(line)
    return s


def check_hook(text, required=()):
    """(True, None), or (False, why) for output that is empty, cut off or lacks a section."""
    s = parse_sections(text) if not isinstance(text, Sections) else text
    if s.empty:
        return False, "empty output"
    if not s.ended:
        return False, "cut off: no === end"
    miss = [n for n in required if n not in s.cmds]
    if miss:
        return False, "no section " + ", ".join(miss)
    return True, None


def rules_of(checks):
    return (checks or {}).get("rules") or {}


def required_image(checks):
    r = rules_of(checks)
    out = ["image-rules", "df"]
    if r.get("kernel") not in (None, "K-none"):
        out.append("kernels")
    if r.get("mounts") == "M-zfs":
        out += ["zfs-list", "zfs-list-cache"]
    return out


def required_after(checks):
    r = rules_of(checks)
    out = ["after-rules", "fstab-check", "mounts"]
    if r.get("boot") not in (None, "B-none"):
        out.append("boot-state")
    if r.get("kernel") not in (None, "K-none", "K-haiku"):
        out.append("kernel")
    return out


def _active(lines, comment="#"):
    return [x.strip() for x in lines or () if x.strip() and not x.strip().startswith(comment)]


def _os_release(sec):
    out = {}
    for x in sec.file("/etc/os-release") or ():
        k, eq, v = x.partition("=")
        if eq:
            out[k.strip()] = v.strip().strip("'\"")
    return out


# --- version order ---------------------------------------------------------------------------

def version_key(s):
    """A sort key: numbers as numbers, the rest as text ("6.1.0-54" is newer than "6.1.0-9")."""
    return [(1, int(t), "") if t.isdigit() else (0, 0, t) for t in re.findall(r"\d+|[A-Za-z]+", str(s))]


def newest(versions):
    return max(versions, key=version_key) if versions else None


# --- S-*: package sources (#19) ----------------------------------------------------------------

def _apt_entries(sec):
    """[(uri, suite)] of the enabled apt sources."""
    out = []
    for p, lines in sorted(sec.glob("/etc/apt/sources.list*").items()):
        if p.endswith(".sources"):
            stanza = {}
            for x in list(lines) + [""]:
                if not x.strip():
                    if stanza and stanza.get("enabled", "yes").lower() not in ("no", "false", "0") \
                            and "deb" in stanza.get("types", "deb").split():
                        out += [(u, s) for u in stanza.get("uris", "").split() for s in stanza.get("suites", "").split()]
                    stanza = {}
                    continue
                if x.startswith("#"):
                    continue
                k, c, v = x.partition(":")
                if c:
                    stanza[k.strip().lower()] = v.strip()
        else:
            for x in _active(lines):
                f = re.sub(r"\[[^\]]*\]", " ", x).split()
                if len(f) >= 3 and f[0] == "deb":
                    out.append((f[1], f[2]))
    return out


def s_apt(sec):
    e = _apt_entries(sec)
    if not e:
        return ["no enabled apt source"]
    out = []
    if any("snapshot.ubuntu.com" in u for u, _ in e):
        out.append("apt still points at snapshot.ubuntu.com")
    if all(u.startswith(("cdrom:", "file:")) for u, _ in e):
        out.append("apt has only the install DVD")
    elif not any(s.endswith("-security") or "/updates" in s for _, s in e):
        out.append("no -security suite in apt's sources")
    return out


def _ini(lines):
    """[(section, {key: value})] of an INI-style file."""
    out, cur = [], None
    for x in lines or ():
        x = x.strip()
        m = re.match(r"\[([^\]]+)\]", x)
        if m:
            cur = {}
            out.append((m.group(1), cur))
        elif cur is not None and "=" in x and not x.startswith("#"):
            k, _, v = x.partition("=")
            cur[k.strip()] = v.strip()
    return out


def s_dnf(sec):
    fed = sec.file("/etc/yum.repos.d/fedora-updates.repo")
    rocky = sec.glob("/etc/yum.repos.d/rocky*.repo")
    if fed is not None:
        u = dict(_ini(fed)).get("updates")
        return [] if u is not None and u.get("enabled") == "1" else ["Fedora's updates repository isn't enabled"]
    if rocky:
        out = []
        enabled = 0
        for lines in rocky.values():
            for _, kv in _ini(lines):
                if kv.get("enabled") == "1":
                    enabled += 1
                    if "dl.rockylinux.org/vault/" in kv.get("baseurl", ""):
                        out.append("an enabled Rocky repository is still the vault")
        if not enabled:
            out.append("no enabled Rocky repository")
        return sorted(set(out))
    return ["no Fedora or Rocky repository file"]


def _servers(sec, path):
    return [x.split("=", 1)[1].strip() for x in _active(sec.file(path)) if re.match(r"Server\s*=", x)]


def s_pac(sec):
    ident = _os_release(sec).get("ID", "")
    main = _servers(sec, "/etc/pacman.d/mirrorlist")
    out = []
    if not main:
        out.append("no Server in /etc/pacman.d/mirrorlist")
    if any("archive.archlinux.org" in x for x in main):
        out.append("pacman still points at archive.archlinux.org")
    if ident.startswith("artix") and main and not any("artix" in x for x in main):
        out.append("Artix's mirrorlist has no Artix mirror")
    if ident == "endeavouros" and not _servers(sec, "/etc/pacman.d/endeavouros-mirrorlist"):
        out.append("no Server in /etc/pacman.d/endeavouros-mirrorlist")
    if ident == "cachyos" and not _servers(sec, "/etc/pacman.d/cachyos-mirrorlist"):
        out.append("no Server in /etc/pacman.d/cachyos-mirrorlist")
    if ident == "garuda" and not _servers(sec, "/etc/pacman.d/chaotic-mirrorlist"):
        out.append("no Server in /etc/pacman.d/chaotic-mirrorlist")
    return out


def s_zyp(sec):
    urls = [kv.get("baseurl", "") for lines in sec.glob("/etc/zypp/repos.d/*.repo").values()
            for _, kv in _ini(lines) if kv.get("enabled", "1") == "1"]
    out = []
    if not urls:
        out.append("no enabled zypper repository")
    if any("download.opensuse.org/history/" in u for u in urls):
        out.append("a zypper repository is still openSUSE's history")
    if any(u.startswith(("cd:", "dvd:")) for u in urls):
        out.append("a zypper repository is the install DVD")
    return out


def s_moss(sec):
    fs = sec.glob("/etc/moss/repo.d/*.kdl")
    sm = sec.file("/etc/moss/system-model.kdl")
    if sm is not None:
        fs = dict(fs, **{"/etc/moss/system-model.kdl": sm})
    if not fs:
        return ["no moss repository file"]
    if any(re.match(r'\s*version\s+"tag/', x) for lines in fs.values() for x in lines):
        return ["a moss repository is still pinned at a tag"]
    return []


def s_apk(sec):
    rep = _active(sec.file("/etc/apk/repositories"))
    if not rep:
        return ["no enabled apk repository"]
    osr = _os_release(sec)
    if osr.get("ID") == "alpine":
        m = re.match(r"(\d+\.\d+)", osr.get("VERSION_ID", ""))
        if m and not any(f"/v{m.group(1)}/" in x or "/edge/" in x for x in rep):
            return ["no apk repository of its branch"]
    return []


def s_xbps(sec):
    n = sum(1 for lines in list(sec.glob("/usr/share/xbps.d/*").values()) + list(sec.glob("/etc/xbps.d/*").values())
            for x in _active(lines) if x.startswith("repository="))
    return [] if n else ["no xbps repository"]


def s_portage(sec):
    """Gentoo: the binhost in binrepos.conf (gentoo.sh's guests take every package they can from it),
    make.conf's getbinpkg, and the gentoo repository's sync-uri (/etc/portage/repos.conf over the
    default in /usr/share/portage/config/repos.conf)."""
    out = []
    bins = [kv.get("sync-uri", "") for lines in list(sec.glob("/etc/portage/binrepos.conf/*").values())
            + [sec.file("/etc/portage/binrepos.conf") or []] for _, kv in _ini(lines)]
    if not any(u.startswith(("https://", "http://")) for u in bins):
        out.append("no binary package host in /etc/portage/binrepos.conf")
    feats = " ".join(x.split("=", 1)[1] for x in _active(sec.file("/etc/portage/make.conf")) if re.match(r"FEATURES\s*=", x))
    if "getbinpkg" not in re.findall(r"[-\w]+", feats):
        out.append("make.conf's FEATURES has no getbinpkg")
    repo = None
    for lines in [sec.file("/usr/share/portage/config/repos.conf") or []] + \
            [v for _, v in sorted(sec.glob("/etc/portage/repos.conf/*").items())] + [sec.file("/etc/portage/repos.conf") or []]:
        for name, kv in _ini(lines):
            if name == "gentoo" and kv.get("sync-uri"):
                repo = kv["sync-uri"]
    if not repo:
        out.append("no sync-uri for the gentoo repository")
    return out


def s_nix(sec):
    t = sec.text("/etc/nixos/configuration.nix")
    if t is None:
        return ["no /etc/nixos/configuration.nix"]
    if "channels.nixos.org" not in t:
        return ["nixPath isn't channels.nixos.org"]
    return []


def _pkg_repos(sec, patterns=("/etc/pkg/*.conf", "/usr/local/etc/pkg/repos/*.conf")):
    """{name: {url, enabled}} of FreeBSD-style pkg repository files, later files over earlier."""
    repos = {}
    for pat in patterns:
        for _, lines in sorted(sec.glob(pat).items()):
            text = "\n".join(x for x in lines if not x.strip().startswith("#"))
            text = re.sub(r"\$\{[^}]*\}", "X", text)   # the ${ABI} in a url isn't a block's end
            for m in re.finditer(r"([A-Za-z0-9_.-]+)\s*:\s*\{(.*?)\}", text, re.S):
                r = repos.setdefault(m.group(1), {})
                # UCL takes a url quoted or bare (DragonFly's df-latest.conf: `url : https://...,`)
                u = re.search(r'\burl\s*[:=]\s*(?:"([^"]*)"|([^\s",;}]+))', m.group(2))
                e = re.search(r"enabled\s*[:=]\s*\"?(yes|no|true|false)\"?", m.group(2), re.I)
                if u:
                    r["url"] = u.group(1) if u.group(1) is not None else u.group(2)
                if e:
                    r["enabled"] = e.group(1).lower() in ("yes", "true")
    return repos


def _enabled_repos(repos):
    return {k: v for k, v in repos.items() if v.get("enabled", True) and v.get("url")}


def s_fbsd(sec):
    en = _enabled_repos(_pkg_repos(sec))
    if not en:
        return ["no enabled pkg repository"]
    if (sec.cmd("pkgbase") or [""])[0:1] == ["yes"] and not any("base" in k.lower() for k in en):
        return ["a pkgbase system with no enabled base repository"]
    return []


def s_gbsd(sec):
    en = _enabled_repos(_pkg_repos(sec))
    if not any("ghostbsd" in (k + v.get("url", "")).lower() for k, v in en.items()):
        return ["no enabled GhostBSD pkg repository"]
    if not any("base" in k.lower() for k in en):
        return ["no enabled GhostBSD base repository"]
    return []


def s_hbsd(sec):
    return [] if any(re.match(r"dnsrec=.+", x) for x in _active(sec.file("/etc/hbsd-update.conf"))) \
        else ["no dnsrec in /etc/hbsd-update.conf"]


def s_nbsd(sec):
    out = []
    if not any(x.startswith("http") for x in _active(sec.file("/usr/pkg/etc/pkgin/repositories.conf"))):
        out.append("no pkgin repository")
    if not any(x.startswith("RELEASEDIR=") for x in _active(sec.file("/usr/pkg/etc/sysupgrade.conf"))):
        out.append("no RELEASEDIR in sysupgrade.conf")
    return out


def s_obsd(sec):
    return [] if any(x.startswith("http") for x in _active(sec.file("/etc/installurl"))) else ["no URL in /etc/installurl"]


def s_dfly(sec):
    en = _enabled_repos(_pkg_repos(sec, ("/usr/local/etc/pkg/repos/*.conf", "/etc/pkg/*.conf")))
    return [] if en else ["no enabled pkg repository"]


def s_ips(sec):
    return [] if any("http" in x for x in sec.cmd("pkg-publisher") or ()) else ["no IPS publisher with an origin"]


def s_haiku(sec):
    return [] if _active(sec.cmd("pkgman-repos")) else ["pkgman lists no repository"]


SOURCE_RULES = {"S-apt": s_apt, "S-dnf": s_dnf, "S-pac": s_pac, "S-zyp": s_zyp, "S-moss": s_moss, "S-apk": s_apk,
                "S-xbps": s_xbps, "S-nix": s_nix, "S-fbsd": s_fbsd, "S-gbsd": s_gbsd, "S-hbsd": s_hbsd,
                "S-nbsd": s_nbsd, "S-obsd": s_obsd, "S-dfly": s_dfly, "S-ips": s_ips, "S-haiku": s_haiku,
                "S-portage": s_portage}


# --- L-*: the loader's default entry (#18) -------------------------------------------------------

def l_grub(sec):
    path = sec.first("/boot/grub/grub.cfg", "/boot/grub2/grub.cfg", "/grub/grub.cfg", "/boot/efi/EFI/grub/grub.cfg")
    if path is None:
        cand = sec.glob("/*/grub.cfg")
        path = sorted(cand)[0] if cand else None
    if path is None:
        return ["no grub.cfg"]
    lines = sec.file(path)
    val = None
    for x in lines:
        m = re.match(r'\s*set default="?([^"\s]*)"?\s*$', x)
        if m and "next_entry" not in m.group(1):
            val = m.group(1)
    env = dict((x.split("=", 1) + [""])[:2] for x in sec.file(sec.first("/boot/grub/grubenv", "/boot/grub2/grubenv") or "") or ()
               if "=" in x and not x.startswith("#"))
    if val is None or val.startswith("$") or val == "saved":
        val = env.get("saved_entry") or "0"
    vers = [m.group(1) for m in (re.search(r"/vmlinuz-([0-9][^\s]*)", x) for x in lines
                                 if re.match(r"\s*linux(efi)?\s", x)) if m]
    top = newest(vers)
    if re.fullmatch(r"\d+", val):
        if int(val) != 0:
            return ["GRUB's default entry isn't the first"]
        if vers and vers[0] != top and version_key(vers[0]) != version_key(top):
            return ["GRUB's first entry isn't the newest kernel"]
        return []
    if ">" in val and re.match(r"\d", val) and top:
        return ["GRUB's default is a sub-menu entry"]
    named = re.search(r"([0-9]+\.[0-9]+[0-9A-Za-z.+~-]*)", val)
    if named and top and version_key(named.group(1).rstrip("-")) < version_key(top) and top not in val:
        return ["GRUB's saved default isn't the newest kernel"]
    return []


def _rescue(name):
    return "rescue" in name


def l_bls(sec):
    entries = [x.strip() for x in sec.cmd("bls-entries") or () if x.strip() and not _rescue(x)]
    entries = [x[:-5] if x.endswith(".conf") else x for x in entries]
    if not entries:
        return ["no boot entries in /boot/loader/entries"]
    top = max(entries, key=version_key)
    p = sec.first("/boot/grub2/grubenv", "/boot/grub/grubenv")
    env = dict((x.split("=", 1) + [""])[:2] for x in sec.file(p) or () if "=" in x and not x.startswith("#")) if p else {}
    saved = env.get("saved_entry")
    if not saved:
        return []
    if saved not in entries:
        return ["saved_entry names no boot entry"]
    return [] if saved == top else ["saved_entry isn't the newest boot entry"]


OLD_ENTRY = re.compile(r"(oldkern|-old\b|previous|fallback)", re.I)


def l_sd(sec):
    entries = [x.strip() for x in sec.cmd("sd-entries") or () if x.strip()]
    entries = [x[:-5] if x.endswith(".conf") else x for x in entries]
    if not entries:
        return ["no systemd-boot entries"]
    conf = sec.first("/boot/efi/loader/loader.conf", "/efi/loader/loader.conf", "/boot/loader/loader.conf")
    dflt = None
    for x in _active(sec.file(conf) if conf else ()):
        m = re.match(r"default\s+(.+)", x)
        if m:
            dflt = m.group(1).strip().strip('"')
    if dflt is not None:
        dflt = dflt[:-5] if dflt.endswith(".conf") else dflt
        hits = [e for e in entries if fnmatch.fnmatchcase(e, dflt)]
        if not hits:
            return ["loader.conf's default matches no entry"]
        return ["loader.conf's default is an old entry"] if all(OLD_ENTRY.search(h) for h in hits) else []
    if len(entries) == 1:
        return []
    chosen = max(entries, key=version_key)
    return ["no default in loader.conf, and the sort's top is an old entry"] if OLD_ENTRY.search(chosen) else []


def l_limine(sec):
    lines = sec.file("/boot/limine.conf")
    if lines is None:
        return ["no /boot/limine.conf"]
    n = sum(1 for x in lines if re.match(r"\s*/[^/+]|\s*/\+", x))
    m = next((re.match(r"\s*default_entry:\s*(\d+)", x) for x in lines if re.match(r"\s*default_entry:", x)), None)
    if not n:
        return ["no entries in limine.conf"]
    if m and int(m.group(1)) > n:
        return ["limine's default_entry is past the last entry"]
    return []


def _loader_kernel_override(lines):
    for x in _active(lines):
        m = re.match(r'kernel\s*=\s*"?([^"\s]*)"?', x)
        if m and m.group(1) != "kernel":
            return True
    return False


def l_fbsd(sec):
    out = []
    if _loader_kernel_override(sec.file("/boot/loader.conf")):
        out.append("loader.conf overrides the kernel")
    z = dict((x.split(None, 1) + [""])[:2] for x in sec.cmd("zfs-boot") or () if x.strip())
    if z.get("bootfs") and z.get("root") and z["bootfs"] != z["root"]:
        out.append("the pool's bootfs isn't the running root")
    return out


def l_nbsd(sec):
    lines = sec.file("/boot.cfg")
    if lines is None:
        return []
    act = _active(lines)
    menus = [x for x in act if x.startswith("menu=")]
    d = next((x.split("=", 1)[1] for x in act if x.startswith("default=")), "1")
    if not re.fullmatch(r"\d+", d) or not 1 <= int(d) <= max(len(menus), 1):
        return ["boot.cfg's default names no menu entry"]
    if menus and re.search(r"onetbsd|boot\s+-s|prompt", menus[int(d) - 1]):
        return ["boot.cfg's default entry isn't the normal boot"]
    return []


def l_obsd(sec):
    for x in _active(sec.file("/etc/boot.conf")):
        m = re.match(r"(?:boot|set image)\s+(\S+)", x)
        if m and not re.search(r"(^|:)/bsd$", m.group(1)):
            return ["/etc/boot.conf boots another kernel"]
    return []


def l_dfly(sec):
    return ["loader.conf overrides the kernel"] if _loader_kernel_override(sec.file("/boot/loader.conf")) else []


def _beadm(lines):
    """[(name, flags, created)] of `beadm list -H` (name;uuid;flags;mountpoint;space;policy;created)."""
    out = []
    for x in lines or ():
        f = x.split(";")
        if len(f) >= 7:
            try:
                created = int(f[6])
            except ValueError:
                created = 0
            out.append((f[0], f[2], created))
    return out


def l_ill(sec):
    bes = _beadm(sec.cmd("beadm"))
    if not bes:
        return ["beadm lists no boot environment"]
    top = max(bes, key=lambda b: b[2])
    return [] if "R" in top[1] else ["the newest boot environment isn't the one to boot next"]


LOADER_RULES = {"L-grub": l_grub, "L-bls": l_bls, "L-sd": l_sd, "L-limine": l_limine, "L-fbsd": l_fbsd,
                "L-nbsd": l_nbsd, "L-obsd": l_obsd, "L-dfly": l_dfly, "L-ill": l_ill}


# --- the other image checks --------------------------------------------------------------------

PSEUDO_SRC = re.compile(r"^(tmpfs|devtmpfs|none|udev|proc|procfs|sysfs|devfs|fdescfs|fdesc|swap|ctfs|mnttab|objfs|sharefs|"
                        r"ptyfs|kernfs|linprocfs|linsysfs|overlay|efivarfs|/dev/loop\d+|/devices/.*|mfs:\d+|bootfs)$")
PSEUDO_TARGET = re.compile(r"^/(proc|sys|dev|run|snap|system/(contract|object)|etc/(mnttab|dfs/sharetab|svc/volatile)|"
                           r"lib/libc\.so\.1|usr/lib/libc/.*|compat/linux/(proc|sys)|var/run)(/|$)")


def _mib(num, unit):
    unit = (unit or "").lower()
    n = float(num)
    return n / 1024 if unit.startswith("k") else n * 1024 if unit.startswith("g") else n * 1048576 \
        if unit.startswith("t") else n / 1048576 if unit in ("", "b", "bytes") else n


def parse_df(lines):
    """[(target, used_pct, free_mib or None)] of the real file systems in `df -Pk`, or of Haiku's
    two-line df (a mount point, then TYPE TOTAL UNIT FREE UNIT FLAGS USED% DEVICE)."""
    out, pend = [], None
    for raw in lines or ():
        x = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).rstrip()
        f = x.split()
        if len(f) >= 6 and re.fullmatch(r"\d+%", f[4]) and f[1].isdigit():
            target = " ".join(f[5:])
            if PSEUDO_SRC.match(f[0]) or PSEUDO_TARGET.match(target) or int(f[1]) == 0:
                continue
            out.append((target, int(f[4][:-1]), int(f[3]) / 1024))
            continue
        if x and x[0] == "/" and len(f) == 1:
            pend = f[0]
            continue
        if pend:
            m = re.match(r"\s*(\S+)\s+([\d.]+)\s*(\w+)\s+([\d.]+)\s*(\w+)\s+\S+\s+(\d+)%", x)
            if m and m.group(1) != "packagefs":
                out.append((pend, int(m.group(6)), _mib(m.group(4), m.group(5))))
            pend = None
    return out


def judge_free(sec, policy):
    p = policy or {}
    fail_at, note_at = int(p.get("free_fail_pct", 90)), int(p.get("free_note_pct", 80))
    boot_min = float(p.get("boot_free_min_mib", 200))
    rows = parse_df(sec.cmd("df"))
    if not rows:
        return Need("FAIL", None, ["df gave no file system"])
    lines, notes = [], []
    for target, pct, free in rows:
        if pct >= fail_at:
            lines.append(f"{target} is at least {fail_at}% full")
        elif pct >= note_at:
            notes.append(f"{target} {pct}% full")
        if (target == "/boot" or target in ("/efi", "/boot/efi") or target.startswith("/boot/efi")) \
                and free is not None and free < boot_min and not (target == "/boot" and sec.cmd("haiku")):
            lines.append(f"{target} has under {boot_min:g} MiB free")
    return verdict_lines(lines, "; ".join(notes) or None)


def judge_kernels(sec, checks):
    rule = rules_of(checks).get("kernel")
    if rule in (None, "K-none"):
        return skip(NO_RULE)
    limit = (checks or {}).get("kernels_max") or KERNELS_MAX.get(rule)
    ks = [x.strip() for x in sec.cmd("kernels") or () if x.strip()]
    if not ks:
        return Need("FAIL", None, ["no kernel found"])
    if rule == "K-abroot":
        return _abroot_kernels(ks, (checks or {}).get("kernels_max") or ABROOT_KERNELS_PER_ROOT)
    if limit and len(ks) > int(limit):
        return Need("FAIL", None, ["more kernels than the updater keeps"])
    if rule == "K-anduinos" and any(x.endswith(" " + NO_INITRD_MARK) for x in ks):
        return Need("FAIL", None, [NO_INITRD])
    if rule == "K-pika" and any(x.endswith(" " + NO_BOOSTER_MARK) for x in ks):
        return Need("FAIL", None, [NO_BOOSTER])
    return Need("PASS", f"{len(ks)} kernel{'s' if len(ks) != 1 else ''}")


def _abroot_kernels(ks, per_root):
    roots = {}
    for x in ks:
        f = x.split()
        if len(f) == 2:
            roots.setdefault(f[0], [])
            if f[1] != "none":
                roots[f[0]].append(f[1])
    lines = [ABROOT_ROOTS] if len(roots) < 2 else []
    lines += [f"no kernel in init/{r}/" for r in sorted(roots) if not roots[r]]
    if any(len(k) > int(per_root) for k in roots.values()):
        lines.append("more kernels than the updater keeps")
    n = sum(len(k) for k in roots.values())
    return Need("FAIL", None, lines) if lines else Need("PASS", f"{n} kernels in {len(roots)} roots")


def judge_zfs_cache(sec):
    """A ZFS dataset (not /) mounted above an fstab mount needs its pool's zfs-list.cache (#20)."""
    tabs = [f[1] for f in (x.split() for x in _active(sec.file("/etc/fstab"))) if len(f) >= 2 and f[1].startswith("/")]
    have = {x.strip() for x in sec.cmd("zfs-list-cache") or () if x.strip()}
    pools = set()
    if not any(x.strip() for x in sec.cmd("zfs-list") or ()):   # zfs answered nothing: no evidence of a clean cache
        return Need("FAIL", None, ["zfs list gave no dataset"])
    for x in sec.cmd("zfs-list") or ():
        f = x.split()
        if len(f) < 2 or not f[1].startswith("/") or f[1] == "/":
            continue
        if any(t.startswith(f[1].rstrip("/") + "/") for t in tabs):
            pools.add(f[0].split("/")[0])
    miss = sorted(p for p in pools if p not in have)
    return verdict_lines([f"no /etc/zfs/zfs-list.cache/{p}" for p in miss],
                         None if pools else "no fstab mount under a ZFS mount")


def judge_image(sec, checks, policy=None, updates=None):
    """Step 2.  {need: Need} for sec (check-image.sh's sections).  UPDATES: image-state's count (0
    or None: never updated, so its sources are as its builder froze them)."""
    r = rules_of(checks)
    out = {}
    src = r.get("source")
    if src in (None, "S-none"):
        out["image:sources"] = skip(NO_RULE)
    elif not updates:
        out["image:sources"] = skip(U0)
    elif src not in SOURCE_RULES:
        out["image:sources"] = Need("FAIL", None, [f"unknown rule {src}"])
    else:
        out["image:sources"] = verdict_lines(SOURCE_RULES[src](sec))
    ld = r.get("loader")
    if ld in (None, "L-none"):
        out["image:loader"] = skip(NO_RULE)
    elif ld not in LOADER_RULES:
        out["image:loader"] = Need("FAIL", None, [f"unknown rule {ld}"])
    else:
        out["image:loader"] = verdict_lines(LOADER_RULES[ld](sec))
    if r.get("mounts") == "M-zfs":
        out["image:zfs-cache"] = judge_zfs_cache(sec)
    out["image:free"] = judge_free(sec, policy)
    out["image:kernels"] = judge_kernels(sec, checks)
    return out


# --- step 3: after a boot ------------------------------------------------------------------------

def parse_mounts(lines):
    """[(target, source, fstype)] in mount order, from check-after.sh's mounts section: its first
    line says the format (mountinfo, mount-p, vfstab-p or mount).  Corpus lists are handed over
    directly to shadowed()."""
    lines = list(lines or ())
    if not lines:
        return []
    fmt = lines[0].split()[1] if lines[0].startswith("format ") and len(lines[0].split()) > 1 else "auto"
    out = []
    for x in lines[1:] if lines[0].startswith("format ") else lines:
        f = x.split()
        if not f:
            continue
        if fmt == "mountinfo" or (fmt == "auto" and " - " in x and len(f) >= 9):
            if " - " not in x:
                continue
            left, right = x.split(" - ", 1)
            lf, rf = left.split(), right.split()
            if len(lf) >= 5 and len(rf) >= 2:
                out.append((lf[4].replace("\\040", " "), rf[1], rf[0]))
        elif fmt == "vfstab-p":   # illumos: device - mountpoint fstype - no options
            if len(f) >= 4:
                out.append((f[2], f[0], f[3]))
        elif fmt == "mount-p":   # FreeBSD: device mountpoint fstype options 0 0
            if len(f) >= 3:
                out.append((f[1], f[0], f[2]))
        else:   # "SRC on TARGET type T (...)" or "SRC on TARGET (T, ...)"
            m = re.match(r"(\S+) on (\S+)(?: type (\S+)| \((\w+))?", x)
            if m:
                out.append((m.group(2), m.group(1), m.group(3) or m.group(4) or ""))
    return out


def shadowed(mounts):
    """Findings for a mount list in mount order: entry i is shadowed when a later entry has the
    same target, or a target that is a parent of i's (/ aside).  An autofs and what it mounts
    on the spot isn't: that is the automount working."""
    out = []
    for i, (t, _src, fs) in enumerate(mounts):
        if fs == "autofs":
            continue
        for tj, _sj, _fj in mounts[i + 1:]:
            if tj == t or (tj != "/" and t.startswith(tj.rstrip("/") + "/")):
                out.append(f"{t} is shadowed by a later mount on {tj}")
                break
    return out


BSD_B = ("B-bsd", "B-obsd", "B-ill", "B-alpbsd")


NO_STATE = "boot-state printed no state line"
NO_SERVICE_LIST = "the service listing printed no ok line"
NO_FSTAB_OK = "the fstab check printed no ok line"
NO_MOUNTS = "the mount list is empty"


def after_lines(sec, checks, steps=None):
    """{need: [line]}, or {need: None} where no rule applies (SKIP), from one reading.  Every
    reading must be positive: a check that printed nothing is a finding, never all clear.
    STEPS: in-guest.sh's steps of the update this reading follows (None for a pre-update one)."""
    r = rules_of(checks)
    out = {n: None for n in AFTER_NEEDS}
    boot, mounts, kernel = r.get("boot"), r.get("mounts"), r.get("kernel")
    bs = sec.cmd("boot-state") or []
    if boot == "B-lin":
        st, fl = [], []
        for x in bs:
            f = x.split(None, 1)
            if len(f) == 2 and f[0] == "state" and f[1] != "running":
                st.append(x)
            elif len(f) == 2 and f[0] == "init" and f[1] == "unknown":
                st.append(x)
            elif len(f) == 2 and f[0] == "failed":
                fl.append(x)
        if not any(x.startswith("state ") for x in bs):
            st.append(NO_STATE)
        out["after:boot-state"], out["after:failed-units"] = st, fl
    elif boot in BSD_B:
        found = [x.strip() for x in bs if x.startswith(("down ", "error "))]
        if not any(x.startswith(("ok ", "error ")) for x in bs):
            found.append(NO_SERVICE_LIST)
        out["after:boot-state"] = found
        out["after:failed-units"] = "boot-state"
    if mounts and mounts != "M-none":
        fc = [x.strip() for x in sec.cmd("fstab-check") or () if x.strip()]
        out["after:fstab"] = [x for x in fc if not x.startswith("ok ")]
        if not any(x.startswith("ok ") for x in fc) and not out["after:fstab"]:
            out["after:fstab"] = [NO_FSTAB_OK]
        if mounts == "M-haiku":
            out["after:shadow"] = "haiku"
        else:
            parsed = parse_mounts(sec.cmd("mounts"))
            out["after:shadow"] = shadowed(parsed) if parsed else [NO_MOUNTS]
    if kernel not in (None, "K-none", "K-haiku"):
        out["after:newest-kernel"] = newest_kernel_lines(sec.cmd("kernel") or [], kernel, sec, steps)
    return out


NOT_NEWEST = "the running kernel isn't the newest installed"
NO_MODULES = "the running kernel has no modules installed"
# K-ostree: /usr/lib/modules is the booted deployment's alone, so running against installed says
# nothing.  What boots is the deployment, so the after-check is which one did (bazzite.md, B7 fix).
OSTREE_NOT_DEFAULT = "the booted deployment isn't the default one"
OSTREE_NOT_STAGED = "the booted deployment isn't the one the update staged"
OSTREE_STAGED = re.compile(r"ostree: deployment ([0-9a-f]+) staged\b")
GRUBENV_KEY = {"default": "grubenv has a default key, which bootupd's grub.cfg boots",
               "next_entry": "grubenv has a next_entry key"}


def _ostree_lines(kv, run, installed, keys, steps):
    out = []
    if run not in installed:
        out.append(NO_MODULES)
    booted, dflt = kv.get("booted"), kv.get("default")
    if not booted or not dflt:
        return out + ["kernel unverified: rpm-ostree status gave no booted or default deployment"]
    if booted != dflt:
        out.append(OSTREE_NOT_DEFAULT)
    staged = [m.group(1) for m in (OSTREE_STAGED.match(s or "") for s in steps or ()) if m]
    if staged and booted.split(".", 1)[0] != staged[-1]:
        out.append(OSTREE_NOT_STAGED)
    out += [GRUBENV_KEY[k] for k in sorted(set(keys)) if k in GRUBENV_KEY]
    return out


def newest_kernel_lines(lines, rule, sec=None, steps=None):
    kv = {}
    installed, keys = [], []
    for x in lines:
        f = x.split(None, 1)
        if len(f) == 2 and f[0] == "installed":
            installed.append(f[1].strip())
        elif len(f) == 2 and f[0] == "grubenv-key":
            keys.append(f[1].strip())
        elif len(f) == 2:
            kv[f[0]] = f[1].strip()
    if "unverified" in kv:
        return [f"kernel unverified: {kv['unverified']}"]
    if rule in ("K-deb", "K-fed", "K-void", "K-moss", "K-ostree", "K-gentoo", "K-anduinos", "K-abroot", "K-pika"):
        run = kv.get("running")
        if not run or not installed:
            return ["kernel unverified: no running or installed kernel listed"]
        if rule == "K-ostree":
            return _ostree_lines(kv, run, installed, keys, steps)
        if rule == "K-abroot":
            # The present root's own modules; what boots is the root the master grub.cfg's
            # default leads to (after an upgrade the new one), and that one has to be running.
            out = [] if run in installed else [NO_MODULES]
            if not kv.get("present") or not kv.get("default"):
                return out + ["kernel unverified: no present or default root"]
            return out + ([] if kv["present"] == kv["default"] else [ABROOT_NOT_DEFAULT])
        if run not in installed:
            return [NO_MODULES]
        return [] if version_key(run) == version_key(newest(installed)) else [NOT_NEWEST]
    if rule == "K-ill":
        bes = _beadm(sec.cmd("beadm-after") if sec is not None else None)
        if not bes:
            return ["kernel unverified: beadm lists no boot environment"]
        top = max(bes, key=lambda b: b[2])
        return [] if "N" in top[1] and "R" in top[1] else [NOT_NEWEST]
    if rule == "K-fbsd":
        if kv.get("k") and kv.get("r"):
            return [] if kv["k"] == kv["r"] else [NOT_NEWEST]
        return ["kernel unverified: freebsd-version gave nothing"]
    m = kv.get("match")
    if m == "yes":
        return []
    if m == "no":
        return [NOT_NEWEST]
    return ["kernel unverified: no match line"]


SKIP_WHY = {"boot-state": "this OS's service state is one list: after:boot-state", "haiku": "no stacked mounts on Haiku"}


def judge_after(before, after, checks, classed=None, steps=None):
    """Step 3.  BEFORE and AFTER are Sections (BEFORE None: a probe, one reading, every line
    standing).  {need: Need}: a line present in both is standing (LIMIT); new after the update,
    FAIL, unless limits.json classes that line (CLASSED {need: {"lines": [...]}}).  STEPS: the
    update's steps (result.json's merge.steps), which AFTER is judged against; never BEFORE."""
    a = after_lines(after, checks, steps if before is not None else None)
    b = after_lines(before, checks) if before is not None else None
    out = {}
    for n in AFTER_NEEDS:
        v = a[n]
        if v is None:
            out[n] = skip(NO_RULE)
        elif isinstance(v, str):
            out[n] = skip(SKIP_WHY.get(v, v))
        else:
            cl = _classed_lines(classed, n)
            old = set(b[n] if b is not None and isinstance(b[n], list) else ())
            standing = [x for x in v if b is None or x in old]
            new = [x for x in v if b is not None and x not in old]
            bad = [x for x in new if x not in cl]
            if bad:
                out[n] = Need("FAIL", None, bad + standing, standing=standing, new=bad)
            elif standing or new:
                out[n] = Need("LIMIT", None, standing + new, standing=standing + new)
            else:
                out[n] = Need("PASS")
    return out


def _classed_lines(classed, need):
    e = (classed or {}).get(need)
    return set((e or {}).get("lines") or ()) if isinstance(e, dict) else set()


def merge_gate(before, after, checks, classed=None, hooks_ok=True, hook_why=None, steps=None):
    """(ok, why, new lines): may this update merge?  No when a hook failed, or an after:* line is
    new against the pre-update boot and limits.json doesn't class it."""
    if not hooks_ok:
        return False, "a hook failed: " + (hook_why or "see hook-*.out"), []
    res = judge_after(before, after, checks, classed, steps)
    new = [f"{n}: {x}" for n in AFTER_NEEDS for x in res[n].new]
    if new:
        return False, "new against the pre-update boot: " + "; ".join(new[:5]), new
    return True, None, []


# --- step 4: new guests ----------------------------------------------------------------------------

FW_SEEN = {"bios": "bios", "q35": "bios", "uefi": "uefi", "uefi-sb": "uefi+secure-boot"}


def fw_matches(claim, seen):
    """The firmware a guest claims in the spec (facts' fw) against FIRMWARE_PROBE's answer."""
    return claim in FW_SEEN and FW_SEEN[claim] == seen


def new_guest_needs(sec, checks):
    """new:firmware (the firmware the guest claims is the one it booted), new:ssh (the hook ran
    at all), new:restic and new:rustic (--version), new:smoke (a backup of /etc and check)."""
    out = {"new:ssh": Need("PASS")}
    fw = (sec.cmd("firmware") or [""])[0].strip()
    claim = (checks or {}).get("fw")
    out["new:firmware"] = Need("PASS", fw) if fw_matches(claim, fw) \
        else Need("FAIL", None, [f"claims {claim}, booted {fw or 'nothing readable'}"])
    for tool, need in (("restic", "new:restic"), ("rustic", "new:rustic")):
        v = _active(sec.cmd(f"{tool}-version"))
        ok = bool(v) and not any(x.startswith("failed") for x in v)
        out[need] = Need("PASS", v[0][:80]) if ok else Need("FAIL", None, [f"{tool}-bko --version failed"])
    sm = [x.strip() for x in sec.cmd("smoke") or () if x.strip()]
    bad = [x for x in sm if x.startswith("failed")]
    out["new:smoke"] = Need("FAIL", None, bad) if bad or not sm else Need("PASS")
    return out


def probe_need(sec):
    """new:probe of a console guest (probe-redox.sh, probe-sculpt.sh)."""
    p = [x.strip() for x in sec.cmd("probe") or () if x.strip()]
    if any(x == "ok" for x in p):
        return Need("PASS")
    return Need("FAIL", None, [x for x in p if x != "ok"] or ["the probe said nothing"])


# --- step 5: the log sweep --------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[78=>]|\x1b")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BRK = re.compile(r"\[\s*\d+\.\d+\]")
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_SYSLOG = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +\d{1,2} \d{2}:\d{2}:\d{2}\b")
_CLOCK = re.compile(r"\b\d{2}:\d{2}:\d{2}\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_RUNDIR = re.compile(r"(?:/mnt/bko-ram/vm-update\.[^\s/]+|\S*/\.vm-update-run\.[^\s/]+|\S*/runs/t2v-[^\s/]+)\S*")
_HEX = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{6,}\b")
_NUM = re.compile(r"\d+")
LINE_MAX = 300
LINES_MAX = 20000


def normalize(line, run_dirs=()):
    """A log line with what differs from boot to boot taken out, or "" for a line with nothing
    left (ANSI and OSC sequences, timestamps, [ 12.345], UUIDs, hex of 6 or more, numbers, the
    run's directories).  Stable: normalize(normalize(x)) == normalize(x)."""
    s = _ANSI.sub("", line)
    s = _CTRL.sub("", s)
    for d in run_dirs or ():
        if d:
            s = s.replace(d, "RUN")
    s = _RUNDIR.sub("RUN", s)
    s = _BRK.sub("", s)
    s = _ISO.sub("TIME", s)
    s = _SYSLOG.sub("TIME", s)
    s = _CLOCK.sub("TIME", s)
    s = _UUID.sub("UUID", s)
    s = _HEX.sub("HEX", s)
    s = _NUM.sub("N", s)
    return " ".join(s.split())[:LINE_MAX]


def normalized_set(text, run_dirs=()):
    out = set()
    for x in (text or "").splitlines()[-LINES_MAX:]:
        n = normalize(x, run_dirs)
        if n:
            out.add(n)
    return out


class Known(list):
    """known_filter's compiled patterns.  .lists: those of them that also head a list, whose
    indented lines (package names, wrapped) are dropped with them."""
    lists = ()


LIST_MARK = "list:"


def known_filter(known, pm=None):
    """The compiled patterns of known-lines.json: "all" and PM's."""
    pats, heads = Known(), []
    for name in ("all", pm):
        for p in (known or {}).get(name) or ():
            is_list = p.startswith(LIST_MARK)
            try:
                rx = re.compile(p[len(LIST_MARK):] if is_list else p)
            except re.error:
                continue
            pats.append(rx)
            if is_list:
                heads.append(rx)
    pats.lists = heads
    return pats


def drop_lists(text, heads):
    """TEXT less the indented lines under a line that a list: pattern matches."""
    if not heads:
        return text
    out, inside = [], False
    for raw in (text or "").splitlines()[-LINES_MAX:]:
        if inside and raw[:1] in (" ", "\t") and raw.strip():
            continue
        out.append(raw)
        n = normalize(raw)
        inside = bool(n) and any(h.search(n) for h in heads)
    return "\n".join(out)


def sweep_lines(sources, known, baseline, older=()):
    """Step 5.  SOURCES {name: text}, KNOWN the compiled patterns, BASELINE the accepted lines or
    None (no baseline yet: every line is new), OLDER texts of the same guest's earlier logs on
    this host (for the seen_before tag only).  [{line, source, seen_before}], sorted."""
    base = set(baseline or ())
    seen = set()
    for t in older or ():
        seen |= normalized_set(t)
    out = {}
    heads = getattr(known, "lists", ())
    for src, text in sorted((sources or {}).items()):
        for n in sorted(normalized_set(drop_lists(text, heads))):
            if n in base or any(p.search(n) for p in known or ()):
                continue
            if n not in out:
                out[n] = {"line": n, "source": src, "seen_before": n in seen}
    return [out[k] for k in sorted(out)]


# --- limits and verdicts ---------------------------------------------------------------------------

def apply_limits(needs, details, classed):
    """NEEDS {need: value}: a LIMIT, or a FAIL of image checks, whose every line limits.json
    classes is LIMIT; any other LIMIT is LIMIT-NEW."""
    out = {}
    for n, v in needs.items():
        cl = _classed_lines(classed, n)
        d = details.get(n)
        lines = list(getattr(d, "lines", None) or [])
        if v == "LIMIT":
            out[n] = "LIMIT" if lines and all(x in cl for x in lines) else "LIMIT-NEW"
        elif v == "FAIL" and n.startswith("image:") and lines and all(x in cl for x in lines):
            out[n] = "LIMIT"
        else:
            out[n] = v
    return out


def verdict_of(needs, harness_why=None):
    """(verdict, class, to_class_only).  To be classed: a FAIL whose only causes are logs: DIFF and
    LIMIT-NEW standing lines; it never reruns and feeds no stop rule."""
    if harness_why:
        return "HARNESS", "harness", False
    bad = {n: v for n, v in needs.items() if v in ("FAIL", "LIMIT-NEW", "DIFF")}
    if not bad:
        return "PASS", "pass", False
    only = all(v in ("DIFF", "LIMIT-NEW") for v in bad.values())
    return "FAIL", "fail", only


def match_flake(guest, tail, flakes):
    """The known flake whose pattern matches vm-update's failure TAIL (result.json's last lines,
    never the whole log, where in-guest.sh's retry loops leave such lines behind)."""
    text = "\n".join(tail) if isinstance(tail, (list, tuple)) else (tail or "")
    for f in flakes or ():
        if f.get("guest") not in (guest, "*"):
            continue
        try:
            if re.search(f.get("pattern") or r"(?!)", text, re.M):
                return f
        except re.error:
            continue
    return None


def only_fail_needs(needs):
    """What the runner feeds StopEngine: FAIL needs only (a LIMIT-NEW doesn't stop a family)."""
    return {n: v for n, v in (needs or {}).items() if v == "FAIL"}


# --- step 6: grades -----------------------------------------------------------------------------------

OFFLINE = ("redox", "sculpt2610")


def grade(guest, row, has_check, backup_ok, generation_pass):
    """The guest's grade (contract 2.1's table).  ROW its newest decisive row at the current key
    (or None); HAS_CHECK testvm/stictest1/G/check.sh exists; BACKUP_OK its stic and holt cells
    have a PASS at their current keys; GENERATION_PASS a PASS at this generation exists."""
    needs = (row or {}).get("needs") or {}
    v = (row or {}).get("verdict")
    if needs.get("try") == "FAIL" or needs.get("new:probe") == "FAIL" or needs.get("new:ssh") == "FAIL":
        return "unusable"
    if guest in OFFLINE:
        return "offline-only"
    if not has_check:
        return "not covered"
    if not generation_pass or v != "PASS":
        return "unverified"
    limit = any(x in ("LIMIT", "LIMIT-NEW") for x in needs.values())
    return "solid" if backup_ok and not limit else "usable"
