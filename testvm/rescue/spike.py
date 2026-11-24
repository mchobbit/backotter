#!/usr/bin/env python3
"""RT3's console spike (docs/design/rescue-tui.md, "The spike (RT2, RT3)"):
boot a platform's rescue image under QEMU and run `bko-rescue --spike` on
each of its consoles, the serial line, the screen and ssh, in BIOS and UEFI.

Usage, on a farm host with the platform's images (chopin, cinqcent), from a
private repo-shaped tree (never the synced testvm/ of sync-hosts.sh):

    cd ~/backotter-work && WEAR_TAG=rescue:spike:PLATFORM ./cmp-run.sh --reserve 2 4 \\
        python3 -B TREE/testvm/rescue/spike.py PLATFORM BKO_RESCUE [--out DIR] [options]

    PLATFORM     linux, freebsd, netbsd, openbsd, dragonfly, illumos or haiku
    BKO_RESCUE   that platform's binary (build-holt.sh HOLT_CRATE=rescue PLATFORM)
    --image IMG  the rescue image; default the newest images/PLATFORM/test/*.img
                 (make-img.sh --test: the serial entry boots, with the screen
                 console too) when images/PLATFORM/done.exit says no build is
                 under way, else /bkoscratch/test2/img-pin/PLATFORM/rescuer.img
    --firmware   bios,uefi (default both; every image boots in both)
    --consoles   serial,screen,ssh (default all three, in each boot)
    --out DIR    results (default ~/backotter-work/rescue-spike/PLATFORM-STAMP)
    --work DIR   the run's RAM directory (default /mnt/bko-ram/rescue-spike-PLATFORM-PID),
                 made empty, removed at the end: the image's overlay, the key,
                 the monitor socket
    --port N     the forwarded ssh port (default the first free one of 2600-2689
                 under ht1-queue.sh's /tmp/bko-ht1-ports locks, held for the run)

Each boot: the image as a 16 GiB qcow2 overlay in --work, through vm-run (the
platform's rescuer guest's vm-run-options less its disks and firmware, and
img-boot.d's disk_bus, as ht1-bsd.sh's +img boots it), its serial console on
our pipe (img-boot.py's way), its human monitor on a unix socket.  At the
serial menu ("Choose [1-4]: ") Shell is picked and sshd started with a key of
this run's (img-boot.py's shell_lines), the shell left again; the binary and
run.sh (the guest's side: stty -a before and after, --profile, --check-tty,
the spike, the spike's panic) are copied to /tmp/bko-spike over ssh.  Then:

  serial  Shell again at the serial menu, `sh run.sh serial size`: the spike
          at the console's own size (agetty sets Linux's ttyS0 to 80x24),
          keys typed at the platform's rate; then `stty rows 0 columns 0`
          and the panic (its frame must say it took 0x0 as 80x24), then
          `stty rows 24 columns 80` and --profile again.  The
          bytes are logged with their times and replayed through screen.py,
          held to --profile's line style, colour and caps, into a text screen
          at each step (serial-NN-STEP.txt).
  screen  sendkey picks Shell at the screen's menu and types `sh run.sh
          screen` and the same keys; a screendump of each step
          (screen-NN-STEP.png), and on Linux /dev/vcsu1's text too.  The PNGs
          are judged by eye against the pass criteria.
  ssh     ssh -tt from this host on an 80x24 pty, LANG=C.UTF-8 and LANG=C
          sent with SendEnv; where the locale doesn't arrive (no AcceptEnv),
          again with it set in the remote command (ssh-*-set).  Replayed as
          the serial line's.

What it checks by itself, per console: --check-tty; stty -a the same after
the spike's quit and after its panic; exit 0 and 101; the cursor shown again
at the end of each stream; echo working afterwards (serial); the key log
after every key (Up Down Left Right Tab Enter Esc Space BS and letters, each
once and right, Esc alone within the timeout); the focus and the notes each
key should give; the gauge reaching 100% with every update under 40 bytes;
the size line; the first frame's bytes and time.  What it can't do on a
console is SKIPPED with the reason.  Results: DIR/summary.json (every check,
every file), DIR/FW/ (PNGs, text screens, the serial log, each console's
byte stream and the guest's files).  Exit 0 when no check FAILed (SKIPPED
and PNGs to judge are not failures), 1 otherwise, 2 on bad usage.
"""

import argparse
import fcntl
import glob
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
TESTVM = os.path.dirname(HERE)
REPO = os.path.dirname(TESTVM)
WORK = os.path.expanduser("~/backotter-work")
sys.path.insert(0, HERE)
import drive  # noqa: E402
import screen  # noqa: E402

PLATFORMS = ("linux", "freebsd", "netbsd", "openbsd", "dragonfly", "illumos", "haiku")
# The rescuer guest whose vm-run-options the image boots with (ht1-bsd.sh's RG).
OPTIONS_GUEST = {"freebsd": "freebsd15-ufs", "netbsd": "netbsd11", "openbsd": "openbsd8",
                 "dragonfly": "dragonfly6", "illumos": "omnios58", "haiku": "haiku6"}
# What the plan's table expects of each console's line style (provisional until RT3).
EXPECT_SCREEN = {"linux": "utf8", "freebsd": "utf8", "netbsd": "dec", "openbsd": "dec",
                 "dragonfly": "ascii", "illumos": "ascii or utf8", "haiku": "utf8"}
GUEST_DIR = "/tmp/bko-spike"
MARK = re.compile(rb"BKO-SPIKE-42-([A-Z-]+)\r?\n")
DONE_TEXT = b"bko-rescue --spike: done"
PORTS = range(2600, 2690)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bt = load("boot_test", os.path.join(REPO, "stick", "boot-test.py"))
imgboot = load("img_boot", os.path.join(TESTVM, "holttest1d", "img-boot.py"))
qd = load("qubes_drive", os.path.join(TESTVM, "qubes", "qubes-drive.py"))
ct = load("console_type", os.path.join(TESTVM, "guix", "console-type.py"))


def note(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ---- the guest's side ----

RUN_SH = r"""# spike.py's guest side: sh run.sh NAME [size].  Its stdin and stdout are the console.
d=%(dir)s n=$1 b=%(dir)s/bko-rescue
stage() { echo "$1" >"$d/$n.stage"; }
mark() { printf 'BKO-SPIKE-%%s-%%s\n' $((6 * 7)) "$1"; }
stage start
stty -a >"$d/$n-stty-before.txt" 2>&1
for v in TERM LANG LC_ALL LC_CTYPE; do eval "echo $v=\${$v-}"; done >"$d/$n-env.txt"
"$b" --profile >"$d/$n-profile.txt" 2>&1
"$b" --check-tty >"$d/$n-check-tty.txt" 2>&1; echo "exit $?" >>"$d/$n-check-tty.txt"
stage spike; mark SPIKE
"$b" --spike 2>"$d/$n-spike.err"; echo $? >"$d/$n-spike.rc"
mark SPIKE-END; stage spike-end
stty -a >"$d/$n-stty-after-quit.txt" 2>&1
# size: the panic at 0x0 (its frame says what it assumed), then 80x24 by stty.
[ "${2:-}" = size ] && stty rows 0 columns 0 && "$b" --profile >"$d/$n-profile-zero.txt" 2>&1
stty -a >"$d/$n-stty-before-panic.txt" 2>&1
stage panic; mark PANIC
"$b" --spike panic 2>"$d/$n-panic.err"; echo $? >"$d/$n-panic.rc"
mark PANIC-END; stage panic-end
stty -a >"$d/$n-stty-after-panic.txt" 2>&1
if [ "${2:-}" = size ]; then
    stty rows 24 columns 80
    "$b" --profile >"$d/$n-profile-sized.txt" 2>&1
fi
stage done; mark DONE
""" % {"dir": GUEST_DIR}

# The keys, in order: (label, key, what the screen shows after it).  A
# label marks a step whose screen is kept.  Each check is (kind, value):
# text on the screen, a focus run, a note, or the gauge's end.
STEPS = [
    ("card1", None, [("text", "spike: card 1 of 4, a dialog"), ("focus", "<  OK  >")]),
    ("tab", "Tab", [("focus", "< Back >")]),
    ("left", "Left", [("focus", "<  OK  >")]),
    ("right", "Right", [("focus", "< Back >")]),
    ("enter", "Enter", [("text", "pressed Back")]),
    ("esc", "Esc", [("text", "Esc: Back")]),
    ("space", "Space", []),
    ("card2", "2", [("text", "spike: card 2 of 4, a list"), ("text", "v more below"),
                    ("focus", "item 1   the first of five")]),
    ("down", "Down", [("focus", "item 2   the second")]),
    ("down-ss3", "DownSS3", [("focus", "item 3   the third"), ("text", "^ more above")]),
    ("up", "Up", [("focus", "item 2   the second")]),
    ("down2", "Down", [("focus", "item 3   the third")]),
    ("chose", "Enter", [("text", "chose item 3")]),
    ("card3", "3", [("text", "spike: card 3 of 4, a gauge")]),
    ("gauge-done", "wait-gauge", [("gauge", None)]),
    ("card4", "4", [("text", "spike: card 4 of 4, a password")]),
    ("typed", "a b c Space BS", [("text", "Password: ***")]),
    ("count", "Enter", [("text", "count typed: 3")]),
    ("leave", "Esc", []),
]
# How each key is logged by the spike (Key::name; typed in the field: *).
LOGGED = {"Tab": "Tab", "Left": "Left", "Right": "Right", "Enter": "Enter", "Esc": "Esc",
          "Space": "Space", "Down": "Down", "DownSS3": "Down", "Up": "Up", "BS": "BS"}
SERIAL_BYTES = {"Tab": b"\t", "Left": b"\x1b[D", "Right": b"\x1b[C", "Enter": b"\r", "Esc": b"\x1b",
                "Space": b" ", "Down": b"\x1b[B", "DownSS3": b"\x1bOB", "Up": b"\x1b[A", "BS": b"\x7f"}
SENDKEY = {"Tab": "tab", "Left": "left", "Right": "right", "Enter": "ret", "Esc": "esc", "Space": "spc",
           "Down": "down", "DownSS3": "down", "Up": "up", "BS": "backspace"}


def expand(key, in_field):
    """A step's keys: [(name as sent, name as logged)]."""
    if key is None or key == "wait-gauge":
        return []
    out = []
    for k in key.split():
        logged = LOGGED.get(k, k)
        if in_field and (len(k) == 1 or k == "Space"):
            logged = "*"
        out.append((k, logged))
    return out


def keys_line(logged):
    """The spike's " keys: ..." line for this history (draw_info's)."""
    k = " keys: " + " ".join(logged[-16:])
    while len(k) > 79:
        k = k[:7] + k[8:]
    return k


class Checks:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail=""):
        """ok: True, False or None (SKIPPED, detail says why) or "judge" (PNGs to look at)."""
        st = {True: "ok", False: "FAIL", None: "SKIPPED", "judge": "JUDGE"}[ok]
        self.items.append({"check": name, "status": st, "detail": str(detail)[:2000]})
        note(f"  {st:7} {name} {str(detail)[:200]}")

    def status(self):
        s = {i["status"] for i in self.items}
        return "FAIL" if "FAIL" in s else "PASS" if "ok" in s else "SKIPPED"


# ---- the serial line ----

class SerialLine:
    """QEMU's stdout (the first serial port) logged whole, with each chunk's time,
    and its stdin; expect() searches from where the last match ended, as
    boot-test.py's Console does, so img-boot.py's bootstrap() can drive it."""

    def __init__(self, proc, log_path, rate):
        self.proc, self.rate = proc, rate
        self.data = bytearray()
        self.times = []                 # (monotonic, offset after the chunk)
        self.pos = 0
        self.eof = False
        self.cv = threading.Condition()
        self.log = open(log_path, "wb")
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        fd = self.proc.stdout.fileno()
        while True:
            try:
                b = os.read(fd, 65536)
            except OSError:
                b = b""
            with self.cv:
                if not b:
                    self.eof = True
                    self.cv.notify_all()
                    return
                self.data += b
                self.times.append((time.monotonic(), len(self.data)))
                self.log.write(b)
                self.log.flush()
                self.cv.notify_all()

    def expect(self, *patterns, timeout):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for i, p in enumerate(patterns):
                    m = p.search(self.data, self.pos)
                    if m:
                        self.pos = m.end()
                        self.last = m
                        return i
                if self.eof:
                    raise EOFError("QEMU exited")
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no match for {[p.pattern for p in patterns]}")
                self.cv.wait(min(left, 1.0))

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        step = 16 if self.rate else len(data)
        for i in range(0, len(data), step):
            self.proc.stdin.write(data[i:i + step])
            self.proc.stdin.flush()
            if self.rate:
                time.sleep(step / self.rate)

    def size(self):
        with self.cv:
            return len(self.data)

    def quiet(self, secs=0.6, most=20.0):
        """Until nothing has come for secs (at most `most`): the offset then."""
        end = time.monotonic() + most
        with self.cv:
            n = len(self.data)
            last = time.monotonic()
            while time.monotonic() < end:
                self.cv.wait(0.1)
                if len(self.data) != n:
                    n, last = len(self.data), time.monotonic()
                elif time.monotonic() - last >= secs:
                    break
            return len(self.data)

    def time_at(self, off):
        """When the byte at offset off came."""
        for t, o in self.times:
            if o > off:
                return t
        return self.times[-1][0] if self.times else time.monotonic()

    def close(self):
        self.log.close()


class PtyLine:
    """An ssh client on drive.py's pty, with the serial line's interface."""

    def __init__(self, argv, env, log_path):
        self.t = drive.Pty(argv, 24, 80, env)
        self.data = bytearray()
        self.times = []
        self.pos = 0
        self.log = open(log_path, "wb")

    def pump(self, secs):
        b = self.t.read(secs)
        if b:
            self.data += b
            self.times.append((time.monotonic(), len(self.data)))
            self.log.write(b)
            self.log.flush()
        return b

    def expect(self, *patterns, timeout):
        end = time.monotonic() + timeout
        while True:
            for i, p in enumerate(patterns):
                m = p.search(self.data, self.pos)
                if m:
                    self.pos = m.end()
                    self.last = m
                    return i
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no match for {[p.pattern for p in patterns]}")
            if self.pump(min(left, 0.2)) is None:
                raise EOFError("ssh hung up")

    def send(self, data):
        os.write(self.t.master, data if isinstance(data, bytes) else data.encode())

    def size(self):
        return len(self.data)

    def quiet(self, secs=0.6, most=20.0):
        end = time.monotonic() + most
        last = time.monotonic()
        while time.monotonic() < end:
            b = self.pump(0.1)
            if b is None:
                break
            if b:
                last = time.monotonic()
            elif time.monotonic() - last >= secs:
                break
        return len(self.data)

    time_at = SerialLine.time_at

    def close(self):
        try:
            self.t.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.t.p.kill()
            self.t.p.wait()
        self.t.close()
        self.log.close()


# ---- the run ----

class Run:
    def __init__(self, a):
        self.a = a
        self.p = a.platform
        self.cfg = imgboot.config(self.p)
        self.user = self.cfg["user"]
        self.summary = {"platform": self.p, "binary": os.path.abspath(a.binary), "image": a.image,
                        "host": os.uname().nodename.split(".")[0],
                        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "boots": {}}
        self.proc = None

    # -- ssh to the image --
    def ssh_base(self):
        return ["ssh", "-i", self.key, "-p", str(self.port), "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10"]

    def ssh(self, cmd, inp=None, timeout=60, env=None):
        r = subprocess.run(self.ssh_base() + [f"{self.user}@127.0.0.1", cmd], input=inp,
                           capture_output=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr

    def guest_file(self, name):
        rc, out, _ = self.ssh(f"cat {GUEST_DIR}/{name} 2>/dev/null")
        return out.decode(errors="replace") if rc == 0 else None

    # -- QEMU --
    def vm_options(self, fw):
        opts = ["--memory", str(self.a.memory)]
        g = OPTIONS_GUEST.get(self.p)
        if g:
            f = os.path.join(WORK, "vms", g, "vm-run-options")
            words = open(f).read().split() if os.path.isfile(f) else []
            i = 0
            while i < len(words):
                w = words[i]
                if w == "--disk":
                    i += 2
                    continue
                if w not in ("--uefi", "--no-secure-boot", "--q35"):
                    opts.append(w)
                i += 1
        with open(os.path.join(TESTVM, "holttest1d", "img-boot.d", self.p + ".json")) as f:
            bus = json.load(f).get("disk_bus")
        if bus and "--disk-bus" not in opts:
            opts += ["--disk-bus", bus]
        if fw == "uefi":
            opts.append("--uefi")
            if self.p != "linux":
                opts.append("--no-secure-boot")
        return opts

    def boot(self, fw, outdir):
        overlay = os.path.join(self.a.work, f"stick-{fw}.qcow2")
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw", "-b",
                        os.path.abspath(self.a.image), overlay, "16G"], check=True)
        self.mon_path = os.path.join(self.a.work, f"mon-{fw}")
        cmd = [os.path.join(TESTVM, "vm-run")] + self.vm_options(fw) + [overlay, "--", "-monitor",
                                                                          f"unix:{self.mon_path},server,nowait"]
        env = dict(os.environ, SSH_PORT=str(self.port), TMPDIR=self.a.work)
        note(f"{fw}: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(os.path.join(outdir, "vm-run.err"), "wb"), env=env)
        rate = self.cfg["rate"] or bt.TYPING_RATE.get(self.p)
        self.con = SerialLine(self.proc, os.path.join(outdir, "serial.log"), rate)

    def halt(self):
        if not self.proc:
            return
        try:
            m = qd.Monitor(self.mon_path)
            m.s.send(b"quit\n")
        except BaseException:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.con.close()
        self.proc = None

    def bring_up(self, ck):
        """The menu, Shell, sshd with the run's key, the shell left; the files copied."""
        got = self.con.expect(bt.MENU, bt.FAILURE, timeout=self.a.boot_timeout)
        if got == 1:
            raise RuntimeError("the boot failed: " + self.con.last.group(0).decode(errors="replace"))
        ck.add("boot: the serial menu", True, f"after {time.monotonic() - self.t0:.0f} s")
        pub = open(self.key + ".pub").read().strip()
        lines = ["bko_g=$(stty -g)"] + imgboot.shell_lines(pub, self.cfg)
        if not imgboot.bootstrap(self.con, bt, lines):
            raise RuntimeError("no shell prompt after Shell at the serial menu")
        if self.con.expect(re.compile(rb"BKO-IMG-SSHD-(\d+)"), timeout=120) != 0 or self.con.last.group(1) != b"0":
            raise RuntimeError("sshd didn't start: " + self.con.last.group(0).decode())
        self.con.send('stty "$bko_g"; exit\n')
        self.con.expect(bt.MENU, timeout=60)
        end = time.monotonic() + 120
        while True:
            rc, _, err = self.ssh("true", timeout=30)
            if rc == 0:
                break
            if time.monotonic() > end:
                raise RuntimeError("ssh to the image never answered: " + err.decode(errors="replace")[-300:])
            time.sleep(3)
        with open(self.a.binary, "rb") as f:
            rc, _, err = self.ssh(f"umask 077; rm -rf {GUEST_DIR}; mkdir {GUEST_DIR} && cat >{GUEST_DIR}/bko-rescue"
                                  f" && chmod 700 {GUEST_DIR}/bko-rescue", inp=f.read(), timeout=120)
        if rc != 0:
            raise RuntimeError("copying the binary: " + err.decode(errors="replace"))
        rc, _, err = self.ssh(f"cat >{GUEST_DIR}/run.sh", inp=RUN_SH.encode())
        rc2, ver, _ = self.ssh(f"{GUEST_DIR}/bko-rescue --version")
        ck.add("ssh up, binary copied", rc == 0 and rc2 == 0, ver.decode(errors="replace").strip())
        rc, uname, _ = self.ssh("uname -a")
        self.summary["boots"][self.fw]["uname"] = uname.decode(errors="replace").strip()

    # -- a stream console (serial, ssh) --
    def stream_console(self, name, line, esc_s, outdir, ck, size_arg):
        """run.sh NAME [size] on LINE, which runs it already or will; the keys, the marks."""
        line.expect(re.compile(rb"BKO-SPIKE-42-SPIKE\r?\n"), timeout=60)
        start = line.pos
        t_start = time.monotonic()
        # The first frame: its title, then quiet.
        line.expect(re.compile(rb"spike: card 1 of 4"), timeout=60)
        line.quiet(0.8, 30)
        first_end = line.size()
        marks = [("card1", first_end)]
        logged = []
        sent_at = {}
        for label, key, _ in STEPS[1:]:
            in_field = label in ("typed",)
            if key == "wait-gauge":
                time.sleep(11)
                line.quiet(0.8, 20)
            for k, lg in expand(key, in_field):
                b = SERIAL_BYTES.get(k, k.encode())
                line.send(b)
                logged.append(lg)
                time.sleep(esc_s + 0.3 if k == "Esc" else 0.05)
            line.quiet(0.6, 20)
            sent_at[label] = time.monotonic()
            marks.append((label, line.size()))
        line.send(b"q")
        try:
            line.expect(re.compile(rb"BKO-SPIKE-42-DONE\r?\n"), timeout=120)
        except (TimeoutError, EOFError) as e:
            ck.add(f"{name}: run.sh reached its end", False, e)
        data = bytes(line.data)
        open(os.path.join(outdir, f"{name}.bytes"), "wb").write(data[start:])
        end = data.find(DONE_TEXT, start)
        first_bytes = first_end - start
        first_s = line.time_at(first_end - 1) - line.time_at(start)
        ck.add(f"{name}: first frame", True, f"{first_bytes} bytes in {first_s:.2f} s")
        return data, start, end, marks, logged, t_start

    def judge_stream(self, name, data, start, end, marks, logged, prof, ck, outdir, assumed_size, sized=False):
        """Replay the spike's bytes through screen.py at each mark; check each step."""
        if end < 0:
            ck.add(f"{name}: the spike's end ('{DONE_TEXT.decode()}')", False, "not in the stream")
            end = len(data)
        rules = drive.rules_from(prof)
        rows, cols = 24, 80
        m = re.match(r"(\d+)x(\d+)", prof.get("size", ""))
        if m:
            cols, rows = int(m[1]), int(m[2])
        s = screen.Screen(rows, cols, rules)
        fed = start
        fault = None
        size_text = ""
        hist = []
        n = 0
        for (label, off), (_, key, checks) in zip(marks, STEPS):
            in_field = label in ("typed",)
            hist += [lg for _, lg in expand(key, in_field)]
            off = min(off, end)
            try:
                s.feed(data[fed:off])
            except screen.Fault as e:
                fault = str(e)
                ck.add(f"{name}: bytes as the profile allows ({rules!r})", False,
                       f"{e}; near: {data[max(start, off - 80):off]!r}")
                break
            fed = off
            n += 1
            with open(os.path.join(outdir, f"{name}-{n:02d}-{label}.txt"), "w") as f:
                f.write(s.dump())
            if n == 1:
                size_text = next((s.row_text(r).strip() for r in range(s.rows)
                                  if s.row_text(r).lstrip().startswith("size")), "")
            text, focus = s.text(), s.focused()
            want = keys_line(hist)
            got = next((s.row_text(r).strip() for r in range(s.rows) if s.row_text(r).lstrip().startswith("keys:")), "")
            fails = [] if got == want.strip() else [f"keys line {got!r}, expected {want.strip()!r}"]
            info = want.strip()
            for kind, v in checks:
                if kind == "text" and v not in text:
                    fails.append(f"no {v!r} on the screen")
                elif kind == "focus" and v not in focus:
                    fails.append(f"focus {focus!r}, expected {v!r}")
                elif kind == "gauge":
                    g = re.search(r"last update (\d+) bytes, the most (\d+) bytes", text)
                    if not g:
                        fails.append("no gauge line")
                        continue
                    info = g[0]
                    if int(g[2]) >= 40:
                        fails.append(f"a gauge update of {g[2]} bytes (the budget: under 40)")
                    if not re.search(r"\b100%", text):
                        fails.append("the gauge isn't at 100%")
            ck.add(f"{name}: step {n:02d} {label}", not fails, "; ".join(fails) or info)
        if fault is None:
            try:
                s.feed(data[fed:end])
                ck.add(f"{name}: bytes as the profile allows ({rules!r})", True,
                       f"{end - start} bytes, {s.clears} clears")
            except screen.Fault as e:
                ck.add(f"{name}: bytes as the profile allows ({rules!r})", False, e)
        want = "size 0x0 (or unknown), taken as 80x24" if assumed_size else f"size {cols}x{rows}"
        ck.add(f"{name}: the size line at the first card", size_text == want, size_text or "(none)")
        spike = data[start:end]
        if rules.hide:
            last_hide, last_show = spike.rfind(b"\x1b[?25l"), spike.rfind(b"\x1b[?25h")
            ck.add(f"{name}: the cursor shown at the quit", last_show > last_hide >= 0,
                   f"?25l at {last_hide}, ?25h at {last_show}")
        else:
            ck.add(f"{name}: the cursor shown at the quit", None, "the profile parks the cursor (no hiding)")
        # The panic's stream: its frame, then the terminal put back.
        pm = re.search(rb"BKO-SPIKE-42-PANIC\r?\n(.*?)BKO-SPIKE-42-PANIC-END", data[end:], re.S)
        if pm:
            pb = pm.group(1)
            open(os.path.join(outdir, f"{name}-panic.bytes"), "wb").write(pb)
            if rules.hide:
                ok = pb.rfind(b"\x1b[?25h") > pb.rfind(b"\x1b[?25l") >= 0
                ck.add(f"{name}: the cursor shown after the panic", ok, f"{len(pb)} bytes")
            ck.add(f"{name}: the panic's frame drew", b"spike: card 1 of 4" in pb, f"{len(pb)} bytes")
            if sized:
                z = b"size 0x0 (or unknown), taken as 80x24" in pb
                ck.add(f"{name}: at 0x0 (stty rows 0 columns 0) the panic's frame assumed 80x24", z,
                       "its size line says so" if z else "no 'size 0x0 (or unknown), taken as 80x24' in its frame")
        else:
            ck.add(f"{name}: the panic's stream", False, "no PANIC .. PANIC-END marks")

    def guest_checks(self, name, ck, outdir, sized):
        """The guest's files: check-tty, stty, exit codes."""
        files = {}
        for f in ("stty-before", "stty-after-quit", "stty-before-panic", "stty-after-panic", "profile",
                  "profile-zero", "profile-sized", "check-tty", "spike.rc", "panic.rc", "spike.err", "panic.err", "env"):
            ext = "" if f.endswith((".rc", ".err")) else ".txt"
            t = self.guest_file(f"{name}-{f}{ext}")
            if t is not None:
                files[f] = t
                with open(os.path.join(outdir, f"{name}-{f}{ext}"), "w") as fh:
                    fh.write(t)
        prof = dict(l.split(" ", 1) for l in files.get("profile", "").splitlines() if " " in l)
        ct_ = files.get("check-tty", "")
        ck.add(f"{name}: --check-tty", ct_.strip().endswith("exit 0"), ct_.strip().replace("\n", " | "))
        b, a = files.get("stty-before"), files.get("stty-after-quit")
        ck.add(f"{name}: stty -a the same after the quit", b is not None and b == a, diff(b, a))
        b, a = files.get("stty-before-panic"), files.get("stty-after-panic")
        ck.add(f"{name}: stty -a the same after the panic", b is not None and b == a, diff(b, a))
        rc = files.get("spike.rc", "").strip()
        ck.add(f"{name}: the spike's exit", rc == "0", f"{rc} {files.get('spike.err', '').strip()}")
        rc = files.get("panic.rc", "").strip()
        err = files.get("panic.err", "")
        ck.add(f"{name}: --spike panic's exit", rc == "101" and "on purpose" in err, f"{rc} {err.strip()[:300]}")
        if sized:
            pz = dict(l.split(" ", 1) for l in files.get("profile-zero", "").splitlines() if " " in l)
            ck.add(f"{name}: --profile's size at 0x0", (pz.get("size") or "").startswith("80x24 (0x0"), pz.get("size"))
            ps = dict(l.split(" ", 1) for l in files.get("profile-sized", "").splitlines() if " " in l)
            ck.add(f"{name}: the size after stty rows 24 columns 80", ps.get("size") == "80x24", ps.get("size"))
        ck.add(f"{name}: --profile", True, "; ".join(f"{k} {prof.get(k)}" for k in
                                                       ("tty", "term", "locale", "size", "rule", "lines", "colour", "caps", "esc")))
        return prof

    def serial(self, outdir, ck):
        self.con.send("1\n")
        self.con.expect(bt.PROMPT, timeout=30)
        self.con.send(f"sh {GUEST_DIR}/run.sh serial size\n")
        # The Esc timeout: the serial lines' (250 ms).
        data, start, end, marks, logged, _ = self.stream_console("serial", self.con, 0.25, outdir, ck, True)
        prof = self.guest_checks("serial", ck, outdir, True)
        ck.add("serial: line style (expected ascii)", prof.get("lines", "").startswith("ascii"), prof.get("lines"))
        self.judge_stream("serial", data, start, end, marks, logged, prof, ck, outdir,
                          prof.get("size", "").startswith("80x24 (0x0"), sized=True)
        # Echo works afterwards.
        self.con.expect(bt.PROMPT, timeout=30)
        mark = self.con.size()
        self.con.send("echo BKO-ECHO-$((6 * 7))\n")
        try:
            self.con.expect(re.compile(rb"BKO-ECHO-42"), timeout=30)
            typed = b"echo BKO-ECHO-$((6 * 7))" in bytes(self.con.data[mark:])
            ck.add("serial: echo works afterwards", typed, "the typed line came back" if typed else "no echo of the typed line")
        except TimeoutError:
            ck.add("serial: echo works afterwards", False, "the command didn't run")
        self.con.send("exit\n")
        try:
            self.con.expect(bt.MENU, timeout=60)
        except TimeoutError:
            pass

    def wait_stage(self, name, want, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = (self.guest_file(f"{name}.stage") or "").strip()
            if st in want:
                return st
            time.sleep(0.5)
        return None

    def vcs_text(self, prof):
        """Linux: tty1's text from /dev/vcsu1 (UTF-32), rows of the console's width."""
        m = re.match(r"(\d+)x(\d+)", prof.get("size", "") if prof else "")
        if self.p != "linux" or not m:
            return None
        rc, out, _ = self.ssh("cat /dev/vcsu1")
        if rc != 0:
            return None
        cols = int(m[1])
        chars = out.decode("utf-32-le", errors="replace")
        return [chars[i:i + cols] for i in range(0, len(chars), cols)]

    def screen(self, outdir, ck):
        try:
            mon = qd.Monitor(self.mon_path, shots=outdir, prefix="screen")
        except SystemExit as e:
            ck.add("screen: the monitor", False, e)
            return
        try:
            self.screen_keys(mon, outdir, ck)
        finally:
            mon.s.close()   # the monitor takes one client at a time: halt()'s quit is next

    def screen_keys(self, mon, outdir, ck):
        def key(k, pause=0.15):
            mon.cmd(f"sendkey {k}")
            time.sleep(pause)

        def typ(s):
            for c in s:
                key(ct.key_name(c), 0.05)
        mon.dump("menu")
        key("1")
        key("ret", 2.0)
        mon.dump("shell")
        typ(f"sh {GUEST_DIR}/run.sh screen")
        key("ret")
        if self.wait_stage("screen", ("spike",), 60) is None:
            mon.dump("no-spike")
            ck.add("screen: run.sh started on the screen", False,
                   "its stage never reached spike in 60 s (screen-*-no-spike.png shows the screen)")
            return
        time.sleep(1.5)
        prof = dict(l.split(" ", 1) for l in (self.guest_file("screen-profile.txt") or "").splitlines() if " " in l)
        esc_s = int((prof.get("esc") or "100ms").rstrip("ms") or 100) / 1000
        vcs, hist = {}, []
        mon.dump("card1")
        vcs["card1"] = self.vcs_text(prof)
        for label, k, _ in STEPS[1:]:
            in_field = label == "typed"
            if k == "wait-gauge":
                time.sleep(11)
            for kk, lg in expand(k, in_field):
                key(SENDKEY.get(kk, kk), esc_s + 0.4 if kk == "Esc" else 0.15)
                hist.append(lg)
            time.sleep(0.8)
            mon.dump(label)
            vcs[label] = self.vcs_text(prof)
        key("q")
        st = self.wait_stage("screen", ("panic-end", "done"), 40)
        time.sleep(0.5)
        mon.dump("after-panic")
        if self.wait_stage("screen", ("done",), 30) is None:
            ck.add("screen: run.sh reached its end", False, f"stage {st}")
        typ("echo BKO-ECHO-OK")
        key("ret", 1.0)
        mon.dump("echo")
        vcs["echo"] = self.vcs_text(prof)
        typ("exit")
        key("ret")
        prof = self.guest_checks("screen", ck, outdir, False)
        ex = EXPECT_SCREEN[self.p]
        ck.add(f"screen: line style (the plan expects {ex})", any(prof.get("lines", "").startswith(x) for x in ex.split(" or ")),
               prof.get("lines"))
        pngs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(outdir, "screen-*.png")))
        ck.add("screen: PNGs to judge against the pass criteria", "judge", " ".join(pngs))
        # The key log on the screen: Linux's /dev/vcsu1 has the text (DownSS3 is a plain
        # Down on a keyboard: sendkey can't send SS3).
        if self.p != "linux" or not any(vcs.values()):
            ck.add("screen: the key log read back", None, "no text of the screen to read on this platform (PNGs only)")
            return
        h = []
        for label, k, checks in STEPS:
            h += [lg for _, lg in expand(k, label == "typed")]
            rows = vcs.get(label)
            if not rows:
                ck.add(f"screen: step {label} (vcsu1)", None, "no /dev/vcsu1 text")
                continue
            text = "\n".join(rows)
            got = next((r.strip() for r in rows if r.strip().startswith("keys:")), "")
            fails = [] if got == keys_line(h).strip() else [f"keys line {got!r}, expected {keys_line(h).strip()!r}"]
            for kind, v in checks:
                if kind == "text" and v not in text:
                    fails.append(f"no {v!r}")
                if kind == "gauge" and not re.search(r"\b100%", text):
                    fails.append("the gauge isn't at 100%")
            with open(os.path.join(outdir, f"screen-vcs-{label}.txt"), "w") as f:
                f.write(text + "\n")
            ck.add(f"screen: step {label} (vcsu1 text)", not fails, "; ".join(fails) or got)
        echo = "\n".join(vcs.get("echo") or [])
        ck.add("screen: echo works afterwards", "echo BKO-ECHO-OK" in echo, "the typed line shows" if "echo BKO-ECHO-OK" in echo else "not on the screen")

    def ssh_console(self, outdir, ck, lang, name, set_in_cmd=False):
        env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "TERM": "xterm", "LANG": lang}
        cmd = f"sh {GUEST_DIR}/run.sh {name}"
        if set_in_cmd:
            cmd = f"env LANG={lang} {cmd}"
        argv = self.ssh_base() + ["-tt", "-o", "SendEnv=LANG", f"{self.user}@127.0.0.1", cmd]
        line = PtyLine(argv, env, os.path.join(outdir, f"{name}.log"))
        try:
            data, start, end, marks, logged, _ = self.stream_console(name, line, 0.1, outdir, ck, False)
        finally:
            line.close()
        prof = self.guest_checks(name, ck, outdir, False)
        want = "utf8" if "UTF-8" in lang else "ascii"
        got = prof.get("locale")
        arrived = got == f"LANG={lang}"
        # The session's own locale with nothing sent (pam_env's /etc/default/locale, say):
        # a LANG that equals it tells nothing about AcceptEnv.
        rc, out, _ = self.ssh('for v in LC_ALL LC_CTYPE LANG; do eval "x=\\${$v-}"; [ -n "$x" ] && { echo "$v=$x"; break; }; done',
                              env={k: v for k, v in os.environ.items() if not k.startswith(("LANG", "LC_"))})
        own = out.decode(errors="replace").strip() or "(none)"
        if set_in_cmd:
            ck.add(f"{name}: the locale arrived (LANG={lang} in the command)", arrived, got)
        elif arrived and own == got:
            ck.add(f"{name}: the locale arrived (LANG={lang} by SendEnv)", None,
                   f"{got}, but that is also the session's own without SendEnv: can't tell")
        elif arrived:
            ck.add(f"{name}: the locale arrived (LANG={lang} by SendEnv)", True, got)
        else:
            ck.add(f"{name}: the locale arrived (LANG={lang} by SendEnv)", None,
                   f"{got} (the session's own: {own}): sshd didn't take LANG (no AcceptEnv, or PAM's environment"
                   f" set over it); run again with it set in the command")
        if arrived:
            ck.add(f"{name}: line style (expected {want})", prof.get("lines", "").startswith(want), prof.get("lines"))
        self.judge_stream(name, data, start, end, marks, logged, prof, ck, outdir, False)
        return arrived

    # -- one boot --
    def one_boot(self, fw):
        outdir = os.path.join(self.a.out, fw)
        os.makedirs(outdir, exist_ok=True)
        ck = Checks()
        self.fw = fw
        self.summary["boots"][fw] = {"options": self.vm_options(fw), "consoles": {}}
        consoles = self.summary["boots"][fw]["consoles"]
        self.t0 = time.monotonic()
        note(f"== {self.p} {fw}")
        try:
            self.boot(fw, outdir)
            up = Checks()
            self.bring_up(up)
            consoles["boot"] = {"status": up.status(), "checks": up.items}
        except (RuntimeError, TimeoutError, EOFError, OSError, subprocess.SubprocessError) as e:
            ck.add("boot and sshd", False, e)
            consoles["boot"] = {"status": "FAIL", "checks": ck.items}
            for c in self.a.consoles:
                consoles[c] = {"status": "SKIPPED", "checks": [{"check": c, "status": "SKIPPED",
                                                                "detail": f"the boot failed: {e}"}]}
            self.halt()
            return
        for c in self.a.consoles:
            ck = Checks()
            note(f"-- {fw} {c}")
            try:
                if c == "serial":
                    self.serial(outdir, ck)
                elif c == "screen":
                    self.screen(outdir, ck)
                else:
                    for lang, name in (("C.UTF-8", "ssh-utf8"), ("C", "ssh-c")):
                        if not self.ssh_console(outdir, ck, lang, name):
                            self.ssh_console(outdir, ck, lang, name + "-set", set_in_cmd=True)
            except (RuntimeError, TimeoutError, EOFError, OSError, subprocess.SubprocessError) as e:
                ck.add(f"{c}: ran to the end", False, f"{type(e).__name__}: {e}")
            consoles[c] = {"status": ck.status(), "checks": ck.items}
        rc, out, _ = self.ssh(f"cd {GUEST_DIR} && tar -cf - --exclude=bko-rescue . 2>/dev/null || tar -cf - *.txt *.rc *.err *.stage")
        if rc == 0 and out:
            gd = os.path.join(outdir, "guest")
            os.makedirs(gd, exist_ok=True)
            subprocess.run(["tar", "-C", gd, "-xf", "-"], input=out)
        self.halt()

    def go(self):
        os.makedirs(self.a.out, exist_ok=True)
        self.key = os.path.join(self.a.work, "key")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "bko-rescue-spike", "-f", self.key],
                       check=True)
        for fw in self.a.firmware:
            self.one_boot(fw)
        self.summary["ended"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        st = [c["status"] for b in self.summary["boots"].values() for c in b["consoles"].values()]
        self.summary["status"] = "FAIL" if "FAIL" in st else "PASS"
        with open(os.path.join(self.a.out, "summary.json"), "w") as f:
            json.dump(self.summary, f, indent=1)
            f.write("\n")
        note(f"{self.summary['status']}: {self.a.out}/summary.json")
        for fw, b in self.summary["boots"].items():
            note(f"  {fw}: " + ", ".join(f"{c} {v['status']}" for c, v in b["consoles"].items()))
        return 0 if self.summary["status"] == "PASS" else 1


def diff(a, b):
    if a is None or b is None:
        return "missing"
    if a == b:
        return "same"
    al, bl = a.split(), b.split()
    return "before only: " + " ".join(x for x in al if x not in bl) + " | after only: " + " ".join(x for x in bl if x not in al)


def find_image(p):
    idir = os.path.join(WORK, "images", p)
    if os.path.isfile(os.path.join(idir, "done.exit")):
        imgs = sorted(glob.glob(os.path.join(idir, "test", "*.img")), key=os.path.getmtime)
        if imgs:
            return imgs[-1]
    pin = f"/bkoscratch/test2/img-pin/{p}/rescuer.img"
    return pin if os.path.isfile(pin) else None


def take_port():
    os.makedirs("/tmp/bko-ht1-ports", exist_ok=True)
    for p in PORTS:
        r = subprocess.run(["ss", "-Hltn", f"sport = :{p}"], capture_output=True, text=True)
        if r.stdout.strip():
            continue
        fd = os.open(f"/tmp/bko-ht1-ports/{p}.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return p, fd
        except OSError:
            os.close(fd)
    raise SystemExit("spike.py: no free port in 2600-2689")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("platform", choices=PLATFORMS)
    ap.add_argument("binary")
    ap.add_argument("--image")
    ap.add_argument("--firmware", default="bios,uefi")
    ap.add_argument("--consoles", default="serial,screen,ssh")
    ap.add_argument("--out")
    ap.add_argument("--work")
    ap.add_argument("--port", type=int)
    ap.add_argument("--memory", type=int, default=3072)
    ap.add_argument("--boot-timeout", type=float, default=900)
    a = ap.parse_args()
    a.firmware = [x for x in a.firmware.split(",") if x]
    a.consoles = [x for x in a.consoles.split(",") if x]
    if not set(a.firmware) <= {"bios", "uefi"} or not set(a.consoles) <= {"serial", "screen", "ssh"}:
        ap.error("--firmware: bios,uefi; --consoles: serial,screen,ssh")
    if not os.access(a.binary, os.R_OK):
        ap.error(f"no binary {a.binary}")
    a.image = a.image or find_image(a.platform)
    if not a.image or not os.path.isfile(a.image):
        ap.error(f"no rescue image for {a.platform} (images/{a.platform}/test/*.img or the img-pin): --image")
    a.out = os.path.abspath(a.out or os.path.join(WORK, "rescue-spike", f"{a.platform}-{time.strftime('%Y%m%d-%H%M%S')}"))
    a.work = os.path.abspath(a.work or f"/mnt/bko-ram/rescue-spike-{a.platform}-{os.getpid()}")
    if os.path.exists(a.work) and os.listdir(a.work):
        ap.error(f"--work {a.work} isn't empty")
    os.makedirs(a.work, mode=0o700, exist_ok=True)
    port_fd = None
    if a.port is None:
        a.port, port_fd = take_port()
    r = Run(a)
    r.port = a.port

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        note(f"spike.py {a.platform}: image {a.image}, binary {a.binary}, port {a.port}, out {a.out}")
        return r.go()
    except KeyboardInterrupt:
        note("stopped")
        return 1
    finally:
        r.halt()
        shutil.rmtree(a.work, ignore_errors=True)
        if port_fd is not None:
            os.close(port_fd)


if __name__ == "__main__":
    sys.exit(main())
