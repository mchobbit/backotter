#!/usr/bin/env python3
"""A terminal that takes exactly the escape sequences bko-rescue sends, and
fails on anything else (docs/design/rescue-tui.md, "Output and profiles",
and RT8).  It keeps what the screen shows, so a test can wait on text and
dump the screen in the golden-screen form of src/screen.rs.

What it takes, and only where the profile says:

    ESC [ r ; c H, ESC [ H     move (inside the screen)
    ESC [ 2 J                  clear            (caps clear)
    ESC [ ? 25 l, ESC [ ? 25 h  hide, show       (caps hide)
    ESC [ ... m                 SGR 0 1 7, and 30-37 40-47 in colour;
                                more than one only where caps say joined
    ESC ( 0, ESC ( B            DEC line drawing (the dec line style only)

and printable characters: ASCII; in utf8, the width-1 letters text.rs
allows and the box glyphs; with DEC on, only the line letters.  No
control byte, no C1, no write past a line's end, and never the screen's
last cell (it would scroll).  Like src/testkit.rs's Checker, in Python,
for a real pty's bytes, which may split a sequence anywhere.

    screen.py [--rows 24] [--cols 80] [--lines utf8|dec|ascii] [--mono]
              [--caps all|none|sgr=joined,cursor=hide,clear=2J] FILE

reads a byte stream (a serial log, a pty capture) and prints the screen
it leaves, or the first fault and exits 1.  Python's stdlib only.
"""

import argparse
import sys

BOX = {"─", "│", "┌", "┐", "└", "┘", "├", "┤", "┬", "┴"}
DEC = {"q": "─", "x": "│", "l": "┌", "k": "┐", "m": "└", "j": "┘",
       "t": "├", "u": "┤", "w": "┬", "v": "┴"}


def allowed(c):
    """text.rs's allowed(): printable ASCII, or a width-1 letter."""
    n = ord(c)
    if 0x20 <= n <= 0x7e:
        return True
    if 0xa0 <= n <= 0xff:
        return c != "­"
    if 0x100 <= n <= 0x24f:
        return True
    if 0x370 <= n <= 0x3ff:
        return n not in (0x378, 0x379, 0x38b, 0x38d, 0x3a2) and not 0x380 <= n <= 0x383
    return 0x400 <= n <= 0x482 or 0x48a <= n <= 0x4ff


# An attribute: (fg, bg, bold, reverse), colours 0-7 or None.
PLAIN = (None, None, False, False)
BACKGROUND = (7, 4, False, False)
DIALOG = (0, 7, False, False)
SHADOW = (None, 0, False, False)


def focus(a):
    return (a[0], a[1], True, True)


def rev(a):
    return (a[0], a[1], a[2], True)


def bold(a):
    return (a[0], a[1], True, a[3])


# screen.rs's LETTERS, in its order.
LETTERS = [
    (PLAIN, "p"), (focus(PLAIN), "P"), (rev(PLAIN), "R"), (bold(PLAIN), "o"),
    (BACKGROUND, "b"), (focus(BACKGROUND), "B"),
    (DIALOG, "d"), (focus(DIALOG), "D"), (rev(DIALOG), "r"), (bold(DIALOG), "h"),
    (SHADOW, "s"),
]

# The letter a plain-looking space takes: a space that isn't reverse
# shows only its background (see `looks`).
LOOKS = {None: PLAIN, 4: BACKGROUND, 7: DIALOG, 0: SHADOW}


def sgr(a):
    """screen.rs's sgr(): an attribute as SGR parameters, in full."""
    p = ["0"]
    if a[2]:
        p.append("1")
    if a[3]:
        p.append("7")
    if a[0] is not None:
        p.append(str(30 + a[0]))
    if a[1] is not None:
        p.append(str(40 + a[1]))
    return ";".join(p)


class Fault(Exception):
    """A byte bko-rescue may not send to this profile."""


class Rules:
    def __init__(self, lines="utf8", colour=True, joined=True, hide=True, clear=True):
        assert lines in ("utf8", "dec", "ascii")
        self.lines, self.colour, self.joined, self.hide, self.clear = lines, colour, joined, hide, clear

    @staticmethod
    def parse_caps(s):
        """`all`, `none`, or --profile's `sgr=joined cursor=hide clear=2J`."""
        if s == "all":
            return True, True, True
        if s == "none":
            return False, False, False
        d = dict(x.split("=", 1) for x in s.replace(",", " ").split())
        return d.get("sgr") == "joined", d.get("cursor") == "hide", d.get("clear") == "2J"

    def __repr__(self):
        return "Rules(%s, %s, sgr=%s cursor=%s clear=%s)" % (
            self.lines, "colour" if self.colour else "mono", "joined" if self.joined else "one-each",
            "hide" if self.hide else "park", "2J" if self.clear else "spaces")


class Screen:
    """What the terminal shows, fed bko-rescue's bytes."""

    MAX_SEQ = 32

    def __init__(self, rows=24, cols=80, rules=None):
        self.rows, self.cols = rows, cols
        self.rules = rules or Rules()
        self.grid = [[(" ", PLAIN) for _ in range(cols)] for _ in range(rows)]
        self.attr = PLAIN
        self.at = None            # (row, col) from 1; None before a move or past a line's end
        self.dec = False
        self.cursor_shown = True
        self.pending = b""        # an escape or a UTF-8 character not yet whole
        self.bytes = 0
        self.clears = 0

    def resize(self, rows, cols):
        """The pty's new size: what's left of the old grid stays at the top left."""
        g = [[(" ", PLAIN) for _ in range(cols)] for _ in range(rows)]
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                g[r][c] = self.grid[r][c]
        self.grid, self.rows, self.cols = g, rows, cols
        self.at = None

    def fault(self, off, what):
        raise Fault("byte %d: %s (%r)" % (off, what, self.rules))

    def feed(self, data):
        """More bytes.  A sequence or character cut at the end waits for
        the rest; anything not allowed raises Fault."""
        buf = self.pending + data
        base = self.bytes - len(self.pending)
        self.bytes += len(data)
        self.pending = b""
        i = 0
        while i < len(buf):
            b = buf[i]
            if b == 0x1b:
                n = self.sequence(buf, i, base + i)
                if n == 0:
                    self.pending = buf[i:]
                    return
                i += n
                continue
            if b < 0x20 or b == 0x7f:
                self.fault(base + i, "control byte 0x%02x" % b)
            if b < 0x80:
                ch = chr(b)
                if self.dec and ch != " ":
                    if ch not in DEC:
                        self.fault(base + i, "%r with DEC line drawing on" % ch)
                    ch = DEC[ch]
                self.cell(base + i, ch)
                i += 1
                continue
            if self.rules.lines != "utf8":
                self.fault(base + i, "byte 0x%02x in a profile that isn't UTF-8" % b)
            n = 2 if b & 0xe0 == 0xc0 else 3 if b & 0xf0 == 0xe0 else 4 if b & 0xf8 == 0xf0 else 0
            if n == 0:
                self.fault(base + i, "invalid UTF-8 lead byte 0x%02x" % b)
            if i + n > len(buf):
                if any(x & 0xc0 != 0x80 for x in buf[i + 1:]):
                    self.fault(base + i, "a UTF-8 character cut short")
                self.pending = buf[i:]
                return
            try:
                ch = buf[i:i + n].decode("utf-8")
            except UnicodeDecodeError:
                self.fault(base + i, "invalid UTF-8 %r" % buf[i:i + n])
            if not (allowed(ch) or ch in BOX):
                self.fault(base + i, "character %r isn't one the screen may show" % ch)
            if self.dec:
                self.fault(base + i, "UTF-8 with DEC line drawing on")
            self.cell(base + i, ch)
            i += n

    def end(self):
        """The stream is over: nothing may be left half sent."""
        if self.pending:
            self.fault(self.bytes - len(self.pending), "the stream ends inside %r" % self.pending)

    def cell(self, off, ch):
        if self.at is None:
            self.fault(off, "a character with no move before it, or past a line's end")
        r, c = self.at
        if (r, c) == (self.rows, self.cols):
            self.fault(off, "the screen's last cell written (it would scroll)")
        self.grid[r - 1][c - 1] = (ch, self.attr)
        self.at = (r, c + 1) if c < self.cols else None

    def sequence(self, buf, i, off):
        """One escape sequence at buf[i]; its length, or 0 if it isn't whole yet."""
        if i + 1 >= len(buf):
            return 0
        kind = buf[i + 1]
        if kind == 0x28:                      # ESC (
            if i + 2 >= len(buf):
                return 0
            f = buf[i + 2]
            if self.rules.lines != "dec" or f not in (0x30, 0x42):
                self.fault(off, "ESC ( %r: DEC charsets only in the dec line style" % chr(f))
            self.dec = f == 0x30
            return 3
        if kind != 0x5b:                      # anything but ESC [
            self.fault(off, "ESC %r" % chr(kind))
        j = i + 2
        while j < len(buf) and not 0x40 <= buf[j] <= 0x7e:
            if j - i >= self.MAX_SEQ:
                self.fault(off, "a CSI sequence over %d bytes" % self.MAX_SEQ)
            j += 1
        if j >= len(buf):
            return 0
        p, fin = buf[i + 2:j].decode("latin-1"), chr(buf[j])
        if fin == "H":
            self.move(p, off)
        elif fin == "J" and p == "2" and self.rules.clear:
            # The current background, as a VT does it.
            a = (None, self.attr[1], False, False)
            self.grid = [[(" ", a) for _ in range(self.cols)] for _ in range(self.rows)]
            self.clears += 1
        elif fin in "hl" and p == "?25" and self.rules.hide:
            self.cursor_shown = fin == "h"
        elif fin == "m":
            self.set_sgr(p, off)
        else:
            self.fault(off, "ESC [ %s %s isn't one this profile sends" % (p, fin))
        return j - i + 1

    def move(self, p, off):
        if p == "":
            self.at = (1, 1)
            return
        rc = p.split(";")
        ok = len(rc) == 2 and all(x.isdigit() for x in rc)
        if ok:
            r, c = int(rc[0]), int(rc[1])
            ok = 1 <= r <= self.rows and 1 <= c <= self.cols
        if not ok:
            self.fault(off, "move %r outside %dx%d" % (p, self.cols, self.rows))
        self.at = (r, c)

    def set_sgr(self, p, off):
        ps = p.split(";")
        if len(ps) > 1 and not self.rules.joined:
            self.fault(off, "SGR %r joined where the profile takes one parameter each" % p)
        for x in ps:
            n = int(x) if x.isdigit() else -1
            colour = 30 <= n <= 37 or 40 <= n <= 47
            if not (n in (0, 1, 7) or (colour and self.rules.colour)):
                self.fault(off, "SGR parameter %r in %r" % (x, p))
            fg, bg, b, r = self.attr
            if n == 0:
                self.attr = PLAIN
            elif n == 1:
                self.attr = (fg, bg, True, r)
            elif n == 7:
                self.attr = (fg, bg, b, True)
            elif n < 40:
                self.attr = (n - 30, bg, b, r)
            else:
                self.attr = (fg, n - 40, b, r)

    # ---- reading it ----

    def row_text(self, r):
        return "".join(ch for ch, _ in self.grid[r])

    def text(self):
        """The screen's characters, a line a row."""
        return "\n".join(self.row_text(r) for r in range(self.rows))

    def find(self, s):
        """(row, col) from 1 where `s` starts on one row, or None."""
        for r in range(self.rows):
            c = self.row_text(r).find(s)
            if c >= 0:
                return r + 1, c + 1
        return None

    def attr_at(self, row, col):
        return self.grid[row - 1][col - 1][1]

    def focused(self):
        """The text in focus (reverse and bold), a string a run, top to bottom."""
        runs, cur = [], ""
        for r in range(self.rows):
            for ch, a in self.grid[r]:
                if a[2] and a[3]:
                    cur += ch
                elif cur:
                    runs.append(cur)
                    cur = ""
            if cur:
                runs.append(cur)
                cur = ""
        return [x.strip() for x in runs if x.strip()]

    @staticmethod
    def looks(ch, a):
        """A space that isn't reverse shows only its background, so it
        takes the plain attribute for it: bko-rescue's diffed redraw
        doesn't resend a space whose foreground or bold alone differ, so
        those depend on what was drawn before and aren't compared."""
        if ch == " " and not a[3]:
            return LOOKS.get(a[1], (None, a[1], False, False))
        return a

    def dump(self, exact=False):
        """The golden form of screen.rs's Canvas::dump: the glyph rows (DEC
        line letters as UTF-8 box glyphs), a blank line, the attribute
        letters, a blank line, the legend.  Spaces as `looks` says, unless
        `exact`."""
        out = [self.text() + "\n"]
        used, others, rows = [], [], []
        for r in range(self.rows):
            line = ""
            for ch, a in self.grid[r]:
                if not exact:
                    a = self.looks(ch, a)
                letter = next((l for x, l in LETTERS if x == a), None)
                if letter is None:
                    if a not in others:
                        others.append(a)
                    letter = str(others.index(a) % 10)
                if (letter, a) not in used:
                    used.append((letter, a))
                line += letter
            rows.append(line)
        out.append("\n" + "\n".join(rows) + "\n\n")
        used.sort(key=lambda x: x[0])
        out.append("".join("%s %s\n" % (l, sgr(a)) for l, a in used))
        return "".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--rows", type=int, default=24)
    ap.add_argument("--cols", type=int, default=80)
    ap.add_argument("--lines", default="utf8", choices=["utf8", "dec", "ascii"])
    ap.add_argument("--mono", action="store_true")
    ap.add_argument("--caps", default="all")
    ap.add_argument("--exact", action="store_true", help="every cell's attribute as sent")
    ap.add_argument("file")
    a = ap.parse_args()
    j, h, c = Rules.parse_caps(a.caps)
    s = Screen(a.rows, a.cols, Rules(a.lines, not a.mono, j, h, c))
    with open(a.file, "rb") as f:
        data = f.read()
    try:
        s.feed(data)
        s.end()
    except Fault as e:
        sys.stdout.write(s.dump(a.exact))
        print("screen.py: %s" % e, file=sys.stderr)
        return 1
    sys.stdout.write(s.dump(a.exact))
    return 0


if __name__ == "__main__":
    sys.exit(main())
