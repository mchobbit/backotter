#!/usr/bin/env python3
"""Drive Haiku's live desktop through QEMU's monitor, for make-haiku.sh.

Haiku's live image boots to a graphical welcome window and has no console on
the serial port, so the only way in is the keyboard: make-haiku.sh boots it
with an HMP monitor on a Unix socket, and this script presses the keys.

Usage: drive.py SOCKET COMMAND [ARG]
  wait-welcome TIMEOUT  wait until the "Welcome to Haiku!" window is up
  try-haiku             choose "Try Haiku" (the window's default button) and
                        wait for the desktop
  terminal              open a Terminal with Tracker: the desktop's "Haiku"
                        volume, system, apps, Terminal.  Tracker doesn't
                        always open a window in time: check for the result
                        and run it again if need be.
  type TEXT             type TEXT (ASCII; \\n is Enter)
  shot FILE.png         save a screenshot, for when something goes wrong

Usage: drive.py serve DIR PORT_FILE
  An HTTP server on 127.0.0.1 (the guest's 10.0.2.2) at a free port, written
  to PORT_FILE: GET serves DIR's files, PUT stores the body as a file in DIR.
  It runs until killed.

Exits 1 if wait-welcome times out.
"""

import http.server
import os
import socket
import struct
import sys
import tempfile
import time
import zlib

SETTLE = 4          # seconds for Tracker to open each window


def hmp(path, command):
    """Run one HMP command and return what the monitor printed."""
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(path)
        s.settimeout(2)

        def read_prompt():
            out = b""
            try:
                while not out.endswith(b"(qemu) "):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    out += chunk
            except socket.timeout:
                pass
            return out

        read_prompt()
        s.sendall(command.encode() + b"\n")
        return read_prompt().decode(errors="replace")


def screen(path):
    """The screen as (width, height, RGB bytes), from a PPM screendump."""
    fd, ppm = tempfile.mkstemp(suffix=".ppm")
    os.close(fd)
    try:
        hmp(path, f"screendump {ppm}")
        with open(ppm, "rb") as f:
            data = f.read()
    finally:
        os.unlink(ppm)
    magic, width, height, _maxval, pixels = data.split(maxsplit=4)
    if magic != b"P6":
        sys.exit(f"drive.py: screendump wrote {magic!r}, not a binary PPM")
    return int(width), int(height), pixels


def save_png(path, out):
    width, height, pixels = screen(path)
    row = width * 3
    raw = b"".join(b"\0" + pixels[y * row:(y + 1) * row] for y in range(height))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    with open(out, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


DESKTOP = (51, 102, 152)    # the default desktop's blue


def welcome_up(path):
    """True while the welcome window is on the desktop.

    That's the desktop's blue in the bottom-left corner (the boot splash has
    none) and a window's yellow title tab in the screen's top half: on the live
    image's first screen, only the welcome window has one.
    """
    width, height, pixels = screen(path)
    i = ((height - 10) * width + 10) * 3
    if any(abs(pixels[i + c] - DESKTOP[c]) > 8 for c in range(3)):
        return False
    yellow = 0
    for y in range(0, height // 2, 2):
        for x in range(0, width, 2):
            i = (y * width + x) * 3
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            if r > 240 and 190 < g < 240 and b < 140:
                yellow += 1
    return yellow > 200


# sendkey names for the characters that aren't letters or digits.
KEYS = {" ": "spc", "\n": "ret", "\t": "tab", "-": "minus", "=": "equal", "/": "slash",
        ".": "dot", ",": "comma", ";": "semicolon", "'": "apostrophe", "\\": "backslash",
        "[": "bracket_left", "]": "bracket_right", "`": "grave_accent"}
SHIFTED = {"_": "minus", "+": "equal", "?": "slash", ">": "dot", "<": "comma",
           ":": "semicolon", '"': "apostrophe", "|": "backslash", "{": "bracket_left",
           "}": "bracket_right", "~": "grave_accent", "!": "1", "@": "2", "#": "3",
           "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0"}


def key(ch):
    if ch.isascii() and ch.isalpha():
        return "shift-" + ch.lower() if ch.isupper() else ch
    if ch.isascii() and ch.isdigit():
        return ch
    if ch in KEYS:
        return KEYS[ch]
    if ch in SHIFTED:
        return "shift-" + SHIFTED[ch]
    sys.exit(f"drive.py: can't type {ch!r}")


def type_text(path, text):
    for ch in text:
        hmp(path, "sendkey " + key(ch))
        time.sleep(0.02)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_PUT(self):
        name = os.path.basename(self.path)
        if not name or name.startswith("."):
            self.send_error(400)
            return
        length = int(self.headers.get("Content-Length", 0))
        with open(os.path.join(self.directory, name), "wb") as f:
            f.write(self.rfile.read(length))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(directory, port_file):
    server = http.server.HTTPServer(
        ("127.0.0.1", 0), lambda *a: Handler(*a, directory=directory))
    with open(port_file, "w") as f:
        f.write(f"{server.server_port}\n")
    server.serve_forever()


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    if sys.argv[1] == "serve":
        serve(*sys.argv[2:4])
        return
    path, command, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    if command == "wait-welcome":
        deadline = time.monotonic() + float(args[0])
        while True:
            try:
                if welcome_up(path):
                    break
            except OSError:
                pass                # QEMU's monitor isn't listening yet
            if time.monotonic() > deadline:
                sys.exit(1)
            time.sleep(3)
        time.sleep(2)               # let it take keyboard focus
    elif command == "try-haiku":
        hmp(path, "sendkey ret")
        deadline = time.monotonic() + 60
        while welcome_up(path) and time.monotonic() < deadline:
            time.sleep(1)
        time.sleep(SETTLE * 2)      # Tracker and Deskbar start after it closes
    elif command == "terminal":
        # Esc closes a menu left open by an earlier try.  Typing a name in
        # Tracker selects it; Alt-Down opens it.
        hmp(path, "sendkey esc")
        time.sleep(1)
        for name in ("Haiku", "system", "apps", "Terminal"):
            type_text(path, name)
            time.sleep(0.8)
            hmp(path, "sendkey alt-down")
            time.sleep(SETTLE)
    elif command == "type":
        type_text(path, args[0])
    elif command == "shot":
        save_png(path, args[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
