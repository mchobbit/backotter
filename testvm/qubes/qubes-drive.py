#!/usr/bin/env python3
"""make-qubes.sh's driver for a booting Qubes OS guest (docs/design/qubes.md).

It talks to QEMU's serial console (Xen's com1, which carries dom0's hvc0)
and to its human monitor (sendkey, screendump), both on unix sockets.  QEMU
logs the serial console itself (the chardev's logfile), so this only reads it.

  qubes-drive.py firstboot SERIAL MONITOR SHOTS PASSPHRASE_FILE PASSWORD_FILE TARBALL
      The first boot after the install: types the LUKS passphrase; drives
      Qubes's initial-setup (its GUI waits for a human by design) with the
      keyboard, checking each step on screendumps; waits for the templates
      and default qubes to be made and for dom0's getty; logs in as tester,
      becomes root, copies TARBALL (testvm/qubes's dom0 files) in through the
      console and runs its revisions.sh; powers off.  Exit 0 if
      revisions.sh said DONE PASS.
  qubes-drive.py check SERIAL MONITOR SHOTS PASSPHRASE_FILE PASSWORD_FILE
      A later boot: types the passphrase, waits for bko-qubes-check.sh's
      "BKO-QUBES-CHECK DONE PASS|FAIL", logs in and powers off.  Exit 0 on
      DONE PASS.
  qubes-drive.py shot MONITOR PNG
      One screendump, as PNG.

SHOTS is a directory for the screendumps (PNG, numbered, named by step).
"""
import base64
import os
import re
import socket
import struct
import sys
import tempfile
import time
import zlib

PASSPHRASE_PROMPT = rb"(?i)(passphrase for [^\n]*:|enter passphrase[^\n]*:?)\s*$"
LOGIN_PROMPT = rb"(?i)login:\s*$"
BOOT_TIMEOUT = 1200         # from QEMU's start to the passphrase prompt
HUB_TIMEOUT = 1800          # from the passphrase to initial-setup's hub
SETUP_TIMEOUT = 3600        # from "Finish configuration" to dom0's getty
SESSION_TIMEOUT = 5400      # revisions.sh
CHECK_TIMEOUT = 3600        # bko-qubes-check.sh

# initial-setup's QUBES OS spoke at 1280x800 (Qubes 4.3.2, docs/design/qubes.md
# "What Q1 found"): each row's vertical centre, and its check box's x.
ROWS = {
    "debian": (140, 377), "fedora": (162, 377), "whonix-template": (184, 377),
    "system-qubes": (290, 377), "disp-firewall-usb": (312, 397), "disp-net": (334, 397),
    "preload": (356, 397), "app-qubes": (378, 377), "usb-qube": (400, 377),
    "whonix-qubes": (488, 377), "vm-pool": (582, 377), "no-config": (694, 377),
}
# What the spoke must show before Done (spec Decision 3); None: either.
WANTED = {
    "debian": True, "fedora": True, "whonix-template": False, "system-qubes": True,
    "app-qubes": True, "usb-qube": False, "vm-pool": True, "no-config": False,
}
SCREEN = (1280, 800)


def die(msg):
    print(f"qubes-drive: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def note(msg):
    print(f"qubes-drive: {time.strftime('%H:%M:%S')} {msg}", flush=True)


def connect(path, tries=120):
    for _ in range(tries):
        try:
            s = socket.socket(socket.AF_UNIX)
            s.connect(path)
            return s
        except OSError:
            time.sleep(1)
    die(f"no socket {path}")


class Serial:
    def __init__(self, path):
        self.s = connect(path)
        self.s.settimeout(1)
        self.buf = b""

    def pump(self):
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return
        if not d:
            raise EOFError("the serial console closed (QEMU exited?)")
        self.buf = (self.buf + d)[-400000:]

    def expect(self, rx, timeout):
        """The first match of rx in what came since the last match, or None
        after timeout seconds."""
        end = time.time() + timeout
        r = re.compile(rx, re.M)
        while True:
            m = r.search(self.buf)
            if m:
                self.buf = self.buf[m.end():]
                return m
            if time.time() > end:
                return None
            self.pump()

    def quiet(self, seconds):
        """Read and drop everything for seconds."""
        end = time.time() + seconds
        while time.time() < end:
            self.pump()
        self.buf = b""

    def send(self, text, delay=0.02):
        for ch in text.encode():
            self.s.send(bytes([ch]))
            time.sleep(delay)


class Monitor:
    def __init__(self, path, shots=None, prefix="setup"):
        self.s = connect(path)
        self.s.settimeout(120)
        self.shots, self.prefix, self.n = shots, prefix, 0
        self.read_prompt()

    def read_prompt(self):
        buf = b""
        while not buf.endswith(b"(qemu) "):
            d = self.s.recv(4096)
            if not d:
                raise EOFError("the monitor closed (QEMU exited?)")
            buf += d
        return buf

    def cmd(self, c):
        self.s.send((c + "\n").encode())
        return self.read_prompt()

    def key(self, k, times=1, pause=0.7):
        for _ in range(times):
            self.cmd(f"sendkey {k}")
            time.sleep(pause)

    def dump(self, label=None):
        """A screendump as (w, h, rgb bytes); saved as SHOTS/NN-label.png if
        label is given."""
        ppm = os.path.join(self.shots or tempfile.gettempdir(), f".qubes-drive-{os.getpid()}.ppm")
        self.cmd(f"screendump {ppm}")
        with open(ppm, "rb") as f:
            img = parse_ppm(f.read())
        os.unlink(ppm)
        if label and self.shots:
            self.n += 1
            write_png(img, os.path.join(self.shots, f"{self.prefix}-{self.n:02d}-{label}.png"))
        return img


def parse_ppm(data):
    parts, i = [], 0
    while len(parts) < 4:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while data[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        parts.append(data[i:j])
        i = j
    i += 1
    if parts[0] != b"P6":
        die("screendump isn't a binary PPM")
    w, h = int(parts[1]), int(parts[2])
    return w, h, data[i:i + w * h * 3]


def write_png(img, path):
    w, h, px = img
    raw = b"".join(b"\0" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def pixel(img, x, y):
    w, _, px = img
    i = (y * w + x) * 3
    return px[i], px[i + 1], px[i + 2]


def lum(img, x, y):
    return sum(pixel(img, x, y)) // 3


def region_diff(a, b, x0, y0, x1, y1):
    """How many pixels of the rectangle differ between screendumps a and b."""
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if pixel(a, x, y) != pixel(b, x, y))


# --- What a screendump shows ---------------------------------------------------------------
def is_hub(img):
    """initial-setup's hub: the dark blue sidebar left, no Done button."""
    if img[:2] != SCREEN:
        return False
    r, g, b = pixel(img, 100, 400)
    dr, dg, db = pixel(img, 20, 43)
    return r < 120 and b < 170 and b > r and not (db > 200 and dr < 80)


def hub_incomplete(img):
    """The hub's orange "Please complete items marked..." bar along the bottom."""
    row = 783
    orange = sum(1 for x in range(200, 1270, 5)
                 if (lambda p: p[0] > 200 and 120 < p[1] < 200 and p[2] < 110)(pixel(img, x, row)))
    return orange > 0.6 * len(range(200, 1270, 5))


def is_spoke(img):
    """The QUBES OS spoke: light grey where the hub's sidebar is, and the blue
    Done button top left."""
    if img[:2] != SCREEN:
        return False
    r, g, b = pixel(img, 100, 400)
    dr, dg, db = pixel(img, 20, 43)
    return r > 230 and g > 230 and db > 200 and dr < 80


def focus(img):
    """The vertical centre of GTK's dotted focus ring, or None: rows with a run
    of 30 or more pixels of its period-3 dotted grey."""
    w, h, px = img
    ys = []
    for y in range(h):
        o = y * w * 3
        v = [(px[o + x * 3] + px[o + x * 3 + 1] + px[o + x * 3 + 2]) // 3 for x in range(w)]
        run = 0
        for x in range(w - 3):
            if 120 <= v[x] <= 240 and abs(v[x] - v[x + 3]) <= 6 and (abs(v[x] - v[x + 1]) > 12 or abs(v[x] - v[x + 2]) > 12):
                run += 1
                if run >= 30:
                    ys.append(y)
                    break
            else:
                run = 0
    return (min(ys) + max(ys)) // 2 if ys else None


def checked(img, row):
    """Whether row's check box (or radio button) is filled with GTK's blue."""
    y, x = ROWS[row]
    blue = sum(1 for yy in range(y - 8, y + 9) for xx in range(x - 8, x + 9)
               if (lambda p: p[2] > 180 and p[0] < 120)(pixel(img, xx, yy)))
    return blue > 100


def sensitive(img, row):
    """Whether row's label is drawn in black (greyed-out labels are ~148)."""
    y, _ = ROWS[row]
    return min(lum(img, x, yy) for yy in range(y - 7, y + 8) for x in range(390, 700)) < 100


def spoke_state(img):
    return {r: (checked(img, r), sensitive(img, r)) for r in ROWS}


def show_state(st):
    return " ".join(f"{r}={'x' if c else '-'}{'' if s else '(grey)'}" for r, (c, s) in st.items())


# --- Steps -----------------------------------------------------------------------------------
def unlock(ser, pp_file):
    pp = open(pp_file).read().strip()
    if not ser.expect(PASSPHRASE_PROMPT, BOOT_TIMEOUT):
        die("no LUKS passphrase prompt on the serial console")
    for _ in range(3):
        time.sleep(1)
        ser.send(pp + "\r")
        # systemd-ask-password redraws its prompt with a * per key typed: drop
        # what comes in meanwhile and for 15 s, then a prompt is a new one.
        ser.quiet(15)
        if not ser.expect(PASSPHRASE_PROMPT, 60):
            note("passphrase typed")
            return
        note("passphrase prompt again: typing it again")
    die("the passphrase was refused 3 times")


def tab_to(mon, row, before):
    """Tab until the focus ring is on row; returns the last screendump."""
    want = ROWS[row][0]
    img = before
    for i in range(16):
        mon.key("tab", pause=1.5)
        img = mon.dump()
        f = focus(img)
        note(f"tab {i + 1}: focus at y={f} (want {want}, {row})")
        if f is not None and abs(f - want) <= 6:
            return img
    mon.dump(f"no-focus-{row}")
    die(f"tabbing never reached {row} (y={want})")


def toggle(mon, row, img):
    """Space on the focused row; checks that its box changed."""
    was = checked(img, row)
    mon.dump(f"before-toggle-{row}")
    mon.key("spc", pause=2)
    after = mon.dump(f"after-toggle-{row}")
    if checked(after, row) == was:
        die(f"space didn't toggle {row}; see the screendumps")
    return after


def initial_setup(ser, mon):
    """Drive initial-setup's hub and QUBES OS spoke to spec Decision 3's
    choices, then Finish configuration."""
    note("waiting for initial-setup's hub")
    end = time.time() + HUB_TIMEOUT
    n = 0
    while True:
        ser.quiet(10)               # (reads the console meanwhile)
        img = mon.dump()
        if is_hub(img) and hub_incomplete(img):
            break
        n += 1
        if n % 30 == 0:
            mon.dump("waiting-for-hub")
        if time.time() > end:
            mon.dump("no-hub")
            die(f"initial-setup's hub didn't show in {HUB_TIMEOUT} s")
    time.sleep(5)                   # let it settle, then take it again
    hub = mon.dump("hub")
    if not (is_hub(hub) and hub_incomplete(hub)):
        die("the hub changed under us")
    # Tab until the QUBES OS spoke's tile is highlighted (it changes look),
    # then Return opens it.
    spoke_tile = (660, 395, 820, 475)
    for i in range(8):
        mon.key("tab", pause=1.5)
        img = mon.dump()
        if region_diff(hub, img, *spoke_tile) > 500:
            note(f"spoke tile focused after {i + 1} tabs")
            break
    else:
        mon.dump("no-spoke-focus")
        die("tabbing never reached the QUBES OS spoke on the hub")
    mon.dump("hub-spoke-focused")
    mon.key("ret", pause=3)
    for _ in range(20):
        img = mon.dump()
        if is_spoke(img):
            break
        time.sleep(3)
    else:
        mon.dump("no-spoke")
        die("Return didn't open the QUBES OS spoke")
    time.sleep(3)
    img = mon.dump("spoke")
    st = spoke_state(img)
    note(f"spoke as it opened: {show_state(st)}")
    if focus(img) is None or abs(focus(img) - 43) > 10:
        note(f"focus at y={focus(img)}, not on Done (y 43): tabbing on regardless")
    # Whonix 18's template off (its qubes then grey out), then sys-usb off.
    for row in ("whonix-template", "usb-qube"):
        if st[row][0]:
            img = tab_to(mon, row, img)
            img = toggle(mon, row, img)
    st = spoke_state(img)
    note(f"spoke before Done: {show_state(st)}")
    wrong = [r for r, want in WANTED.items() if st[r][0] != want]
    if st["whonix-qubes"][0] and st["whonix-qubes"][1]:
        wrong.append("whonix-qubes (ticked, not greyed)")
    if wrong:
        mon.dump("spoke-wrong")
        die(f"the spoke isn't as wanted: {', '.join(wrong)}")
    mon.dump("spoke-before-done")
    mon.key("alt-d", pause=3)
    for _ in range(20):
        img = mon.dump()
        if is_hub(img):
            break
        time.sleep(3)
    else:
        mon.dump("no-hub-after-done")
        die("Done didn't go back to the hub")
    time.sleep(3)
    img = mon.dump("hub-after-done")
    if hub_incomplete(img):
        die("the hub still says the spoke is incomplete")
    mon.key("alt-f", pause=5)
    after = mon.dump("after-finish")
    if region_diff(img, after, 192, 0, 1280, 800) < 2000:
        time.sleep(10)
        after = mon.dump("after-finish-2")
        if region_diff(img, after, 192, 0, 1280, 800) < 2000:
            die("Finish configuration didn't start anything")
    note("initial-setup is making the templates and qubes")


def login(ser, pw_file, timeout):
    """Log in as tester on hvc0 and become root, with a quiet prompt BKO# ."""
    pw = open(pw_file).read().strip()
    # Kernel messages (audit lines) can land on the prompt's own line after
    # "dom0 login: ", and an Enter sent before the getty is up is lost
    # (Q1's first check boot): ask again every 15 s until a prompt ends a line.
    end = time.time() + timeout
    while not ser.expect(LOGIN_PROMPT, 15):
        if time.time() > end:
            die(f"no login prompt in {timeout} s")
        ser.send("\r")
    time.sleep(2)
    ser.send("tester\r")
    if not ser.expect(rb"(?i)password:\s*$", 60):
        die("no password prompt")
    ser.send(pw + "\r")
    if not ser.expect(rb"\$ $", 120):
        die("no shell prompt after logging in")
    ser.send("sudo -i\r")
    m = ser.expect(rb"(?i)(password for tester:|# $)", 60)
    if m and b"password" in m.group(0).lower():
        ser.send(pw + "\r")
        m = ser.expect(rb"# $", 60)
    if not m:
        die("sudo -i gave no root prompt")
    ser.send("stty -echo cols 250; dmesg -n 1; export PS1='BKO# ' TERM=dumb\r")
    if not ser.expect(rb"BKO# $", 30):
        die("no BKO# prompt")
    note("logged in, root")


def poweroff(ser):
    ser.send("systemctl poweroff\r")
    try:
        ser.quiet(5)
    except EOFError:
        pass


def send_file(ser, data, dest):
    """Types data into dest in dom0 as base64, and checks its SHA-256."""
    import hashlib
    want = hashlib.sha256(data).hexdigest()
    b64 = base64.encodebytes(data).decode().splitlines()
    for delay in (0.008, 0.03):
        t0 = time.time()
        ser.send(f"base64 -d >{dest} <<'BKOEOF'\r")
        time.sleep(1)
        for line in b64:
            ser.send(line + "\r", delay)
        ser.send("BKOEOF\r")
        if not ser.expect(rb"BKO# $", 120):
            ser.send("\x03")      # a damaged BKOEOF: end the here-document
            ser.expect(rb"BKO# $", 30)
        ser.send(f"echo BKO-SUM $(sha256sum <{dest} | cut -c1-64)\r")
        m = ser.expect(rb"BKO-SUM ([0-9a-f]{64})", 60)
        got = m.group(1).decode() if m else None
        note(f"sent {len(data)} bytes to {dest} in {time.time() - t0:.0f} s ({delay * 1000:.0f} ms a key): sha256 {got}")
        ser.expect(rb"BKO# $", 30)
        if got == want:
            return
        ser.send("\x03")
        ser.expect(rb"BKO# $", 30)
    die(f"{dest} arrived damaged twice (sha256 isn't {want})")


def firstboot(sock, mon_sock, shots, pp_file, pw_file, tarball):
    ser = Serial(sock)
    mon = Monitor(mon_sock, shots)
    unlock(ser, pp_file)
    initial_setup(ser, mon)
    # The templates and qubes take ~10 min; dom0's getty comes after.
    end = time.time() + SETUP_TIMEOUT
    n = 0
    while not ser.expect(LOGIN_PROMPT, 300):
        n += 1
        mon.dump(f"progress-{n}")
        if time.time() > end:
            die(f"initial-setup didn't end in {SETUP_TIMEOUT} s")
    mon.dump("setup-done")
    note("initial-setup done: dom0's login prompt")
    ser.buf = b"login: "            # login() expects it again
    login(ser, pw_file, 60)
    send_file(ser, open(tarball, "rb").read(), "/root/bko-qubes.tgz")
    ser.send("rm -rf /root/bko-qubes; mkdir /root/bko-qubes && tar -xzf /root/bko-qubes.tgz -C /root/bko-qubes &&"
             " bash /root/bko-qubes/revisions.sh 2>&1 | tee /root/bko-qubes/revisions.out; echo BKO-SETUP-END\r")
    m = ser.expect(rb"BKO-QUBES-SETUP DONE (PASS|FAIL)", SESSION_TIMEOUT)
    if not m:
        die(f"revisions.sh didn't finish in {SESSION_TIMEOUT} s")
    ser.expect(rb"BKO-SETUP-END", 60)
    ser.expect(rb"BKO# $", 30)
    note(f"revisions.sh: DONE {m.group(1).decode()}")
    poweroff(ser)
    return 0 if m.group(1) == b"PASS" else 1


def check(sock, mon_sock, shots, pp_file, pw_file):
    ser = Serial(sock)
    mon = Monitor(mon_sock, shots, "check")
    unlock(ser, pp_file)
    m = ser.expect(rb"BKO-QUBES-CHECK DONE (PASS|FAIL)", CHECK_TIMEOUT)
    if not m:
        mon.dump("check-timeout")
        die(f"no BKO-QUBES-CHECK DONE in {CHECK_TIMEOUT} s")
    note(f"BKO-QUBES-CHECK DONE {m.group(1).decode()}")
    ser.send("\r")
    login(ser, pw_file, 120)
    poweroff(ser)
    return 0 if m.group(1) == b"PASS" else 1


def main():
    a = sys.argv[1:]
    try:
        if a[:1] == ["firstboot"] and len(a) == 7:
            return firstboot(*a[1:])
        if a[:1] == ["check"] and len(a) == 6:
            return check(*a[1:])
        if a[:1] == ["shot"] and len(a) == 3:
            write_png(Monitor(a[1], None).dump(), a[2])
            return 0
    except EOFError as e:
        die(str(e))
    sys.exit(__doc__.strip())


if __name__ == "__main__":
    sys.exit(main())
