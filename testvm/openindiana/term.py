"""A small terminal emulator, just enough of xterm's control sequences for
OpenIndiana's text installer (curses, TERM=xterm) on a serial console: it
keeps an 80x24 screen of characters, without attributes, so that a driver
can read what the installer shows.

    t = Term(); t.feed(data); t.text() -> the screen, one line per row
Run as a script, it prints the screen at the end of a console log:
    python3 -I term.py CONSOLE_LOG
"""
import re
import sys

CSI = re.compile(rb"\x1b\[([0-9;?>]*)([ -/]*)([@-~])")


class Term:
    def __init__(self, rows=24, cols=80):
        self.rows, self.cols = rows, cols
        self.pending = b""
        self.clear()

    def clear(self):
        self.s = [[" "] * self.cols for _ in range(self.rows)]
        self.r = self.c = 0

    def text(self):
        return "\n".join("".join(row).rstrip() for row in self.s)

    def _put(self, ch):
        if self.c >= self.cols:
            self.c = 0
            self._lf()
        self.s[self.r][self.c] = ch
        self.c += 1

    def _lf(self):
        if self.r == self.rows - 1:
            self.s.pop(0)
            self.s.append([" "] * self.cols)
        else:
            self.r += 1

    def _csi(self, params, final):
        p = [int(x) if x.isdigit() else 0 for x in params.lstrip("?>").split(";")] if params else []
        n = (p[0] if p and p[0] else 1)
        if final in "Hf":
            self.r = min(max((p[0] if len(p) > 0 and p[0] else 1) - 1, 0), self.rows - 1)
            self.c = min(max((p[1] if len(p) > 1 and p[1] else 1) - 1, 0), self.cols - 1)
        elif final == "d":
            self.r = min(n - 1, self.rows - 1)
        elif final == "G":
            self.c = min(n - 1, self.cols - 1)
        elif final == "A":
            self.r = max(self.r - n, 0)
        elif final == "B":
            self.r = min(self.r + n, self.rows - 1)
        elif final == "C":
            self.c = min(self.c + n, self.cols - 1)
        elif final == "D":
            self.c = max(self.c - n, 0)
        elif final == "X":
            for i in range(self.c, min(self.c + n, self.cols)):
                self.s[self.r][i] = " "
        elif final == "K":
            m = p[0] if p else 0
            rng = range(self.c, self.cols) if m == 0 else range(0, self.c + 1) if m == 1 else range(self.cols)
            for i in rng:
                self.s[self.r][i] = " "
        elif final == "J":
            m = p[0] if p else 0
            if m == 2:
                self.s = [[" "] * self.cols for _ in range(self.rows)]
            elif m == 0:
                self._csi("", "K")
                for r in range(self.r + 1, self.rows):
                    self.s[r] = [" "] * self.cols
        elif final == "P":
            row = self.s[self.r]
            del row[self.c:self.c + n]
            row.extend([" "] * (self.cols - len(row)))
        elif final == "@":
            row = self.s[self.r]
            for _ in range(n):
                row.insert(self.c, " ")
            del row[self.cols:]
        elif final == "b":   # repeat the last character
            last = self.s[self.r][self.c - 1] if self.c else " "
            for _ in range(n):
                self._put(last)
        # m, h, l, r, t and the rest: attributes and modes, nothing on screen.

    def feed(self, data: bytes):
        # A sequence cut off at the end of one read waits for the next.
        data = self.pending + data
        self.pending = b""
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0x1b:
                m = CSI.match(data, i)
                if m:
                    self._csi(m.group(1).decode(), m.group(3).decode())
                    i = m.end()
                    continue
                rest = data[i:]
                if len(rest) < 2 or (len(rest) < 3 and rest[1:2] in b"()") or \
                        (rest[1:2] == b"[" and len(rest) < 40 and re.fullmatch(rb"\x1b\[[0-9;?>]*[ -/]*", rest)):
                    self.pending = rest
                    return
                # ESC ( B, ESC =, ESC >, ESC 7/8 and the like: two or three bytes.
                i += 3 if i + 1 < len(data) and data[i + 1] in b"()" else 2
                continue
            i += 1
            if b == 0x0d:
                self.c = 0
            elif b == 0x0a:
                self._lf()
            elif b == 0x08:
                self.c = max(self.c - 1, 0)
            elif b == 0x0c:
                self.clear()
            elif b == 0x09:
                self.c = min((self.c // 8 + 1) * 8, self.cols - 1)
            elif b >= 0x20:
                self._put(chr(b) if b < 0x7f else " ")


if __name__ == "__main__":
    t = Term()
    t.feed(open(sys.argv[1], "rb").read())
    print(t.text())
