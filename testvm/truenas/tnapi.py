#!/usr/bin/env python3
"""A small JSON-RPC 2.0 client over a websocket, Python's standard library
only, for TrueNAS's two APIs (docs/design/truenas.md, Decision 1):

- the installer's (`truenas_installer --server`, ws://HOST:PORT/ws behind the
  live ISO's nginx): adopt, list_disks, install, reboot, shutdown;
- the middleware's (wss://HOST:PORT/api/current on the installed system):
  auth.login_ex, then any method.

Usage as a program, one call a run (each prints the result as JSON):
    tnapi.py URL METHOD [JSON-PARAMS...]
    tnapi.py --login USER PASSWORD-FILE URL METHOD [JSON-PARAMS...]
    tnapi.py --wait SECONDS URL METHOD [JSON-PARAMS...]   retry the connection
A `--job` flag waits for a middleware job (the call returns its id) and
prints its result.  Server notifications (the installer's
installation_progress) go to stderr as they come.

TLS certificates aren't checked: the guests' are self-signed, on 127.0.0.1
behind QEMU's user network.  Passwords are read from files, never argv.
"""

import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.parse


class RpcError(Exception):
    pass


class Ws:
    """One websocket connection: text frames out (masked), text frames in."""

    def __init__(self, url, timeout=60):
        u = urllib.parse.urlsplit(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=u.hostname)
        self.s = raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            c = self.s.recv(1)
            if not c:
                raise ConnectionError("closed during the handshake")
            head += c
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise ConnectionError(f"handshake refused: {status.decode(errors='replace')}")
        self.buf = b""

    def _read(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c:
                raise ConnectionError("connection closed")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise ConnectionError("closed by the server")
            if op == 0x9:  # ping: pong back (masked, as a client must)
                m = os.urandom(4)
                self.s.sendall(bytes([0x8A, 0x80 | len(data)]) + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))
                continue
            if op in (0x1, 0x0):
                msg += data
                if b0 & 0x80:
                    return msg.decode()


class Client:
    def __init__(self, url, timeout=60):
        self.ws = Ws(url, timeout)
        self.n = 0

    def call(self, method, *params, timeout=None):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": my, "method": method, "params": list(params)}))
        if timeout is not None:
            self.ws.s.settimeout(timeout)
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == my:
                if "error" in m and m["error"] is not None:
                    raise RpcError(json.dumps(m["error"]))
                return m.get("result")
            if "method" in m:  # a notification: progress and the like
                print(json.dumps(m.get("params")), file=sys.stderr, flush=True)

    def wait_job(self, job_id, timeout=3600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            jobs = self.call("core.get_jobs", [["id", "=", job_id]])
            if jobs:
                j = jobs[0]
                if j["state"] == "SUCCESS":
                    return j.get("result")
                if j["state"] in ("FAILED", "ABORTED"):
                    raise RpcError(f"job {job_id} {j['state']}: {j.get('error')}")
                p = j.get("progress") or {}
                print(json.dumps({"job": job_id, "percent": p.get("percent"), "description": p.get("description")}),
                      file=sys.stderr, flush=True)
            time.sleep(5)
        raise RpcError(f"job {job_id} didn't finish in {timeout} s")


def main(argv):
    login = None
    wait = 0
    job = False
    while argv and argv[0].startswith("--"):
        if argv[0] == "--login":
            with open(argv[2]) as f:
                login = (argv[1], f.read().strip())
            argv = argv[3:]
        elif argv[0] == "--wait":
            wait = int(argv[1])
            argv = argv[2:]
        elif argv[0] == "--job":
            job = True
            argv = argv[1:]
        else:
            sys.exit(f"tnapi: unknown option {argv[0]}")
    if len(argv) < 2:
        sys.exit(__doc__)
    url, method, params = argv[0], argv[1], [json.loads(p) for p in argv[2:]]
    t0 = time.time()
    while True:
        try:
            c = Client(url)
            if login:
                r = c.call("auth.login_ex", {"mechanism": "PASSWORD_PLAIN", "username": login[0], "password": login[1]})
                if (r or {}).get("response_type") != "SUCCESS":
                    raise RpcError(f"login refused: {json.dumps(r)}")
            break
        except (OSError, ConnectionError, RpcError) as e:
            if time.time() - t0 >= wait:
                sys.exit(f"tnapi: {url}: {e}")
            time.sleep(5)
    try:
        r = c.call(method, *params, timeout=7200)
        if job:
            r = c.wait_job(r)
    except RpcError as e:
        sys.exit(f"tnapi: {method}: {e}")
    print(json.dumps(r, indent=1, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
