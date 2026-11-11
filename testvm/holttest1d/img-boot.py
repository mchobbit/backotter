#!/usr/bin/env python3
"""Run a rescue image under QEMU with its serial console on our stdin and stdout
(vm-run with no --serial-log: -serial stdio), log the console to LOG, and at each
BackOtter console menu pick "Shell" and start an sshd that takes the run's key.

Usage: img-boot.py LOG PLATFORM PUBKEY_FILE -- COMMAND ARGS...

HoltTest's +img variant (README; ht1.sh's HT_VARIANT=img): the image's sshd is off
and refuses root by its own config, and no key is in any image, so a per-run key is
typed here: the public half goes to /tmp/bko-k, host keys are made, and sshd starts
with command-line options, which win over the image's own settings (AllowUsers adds
up).  Nothing in the image changes.  img-boot.d/PLATFORM.json holds what differs per
system (see its "about").  The menu, the prompt and the typing rates are
stick/boot-test.py's, so the two can't drift apart.  It re-arms at every "Choose
[1-4]: ": a rescuer that reboots during a rebuild (Haiku's does) gets it again.
Exits with the command's status.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
BOOT_TEST = os.path.join(os.path.dirname(os.path.dirname(HERE)), "stick", "boot-test.py")


def load_boot_test():
    spec = importlib.util.spec_from_file_location("boot_test", BOOT_TEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def config(platform):
    """img-boot.d/PLATFORM.json, with its defaults filled in."""
    with open(os.path.join(HERE, "img-boot.d", platform + ".json"), encoding="utf-8") as f:
        c = json.load(f)
    return {"user": c.get("user") or "root", "extra": list(c.get("extra") or []), "rate": c.get("rate")}


def shell_lines(pubkey, cfg):
    """What is typed at the root shell, in order.  Echo goes off first (the key needn't be
    in the log), and then the system's own lines (FreeBSD's libedit must be off before a
    long one).  The key file's umask is its subshell's only: sshd and every SSH session
    it starts inherit the shell's, and a run's rebuild (grub-install, update-grub in the
    chroot) must write with the image's default, as a console user's would, not 077.
    ssh-keygen -A: the images have no host keys."""
    return (["export TERM=dumb; stty -echo"] + list(cfg["extra"])
            + ["(umask 077; printf '%s\\n' " + shquote(pubkey) + " >/tmp/bko-k)", "ssh-keygen -A",
               "$(command -v sshd) -o AuthorizedKeysFile=/tmp/bko-k -o StrictModes=no"
               f" -o PermitRootLogin=prohibit-password -o AllowUsers={cfg['user']} -o PasswordAuthentication=no",
               "echo BKO-IMG-SSHD-$?"])


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def bootstrap(con, bt, lines, wait_prompt=30.0):
    """At a menu that has just been matched: "Shell", then the lines, each after the shell's
    prompt.  True once the last is typed; False when a prompt didn't come (the next menu
    will try again)."""
    con.send("1\n")
    try:
        con.expect(bt.PROMPT, timeout=wait_prompt)
        for line in lines:
            con.send(line + "\n")
            time.sleep(0.2)
    except TimeoutError:
        return False
    return True


def main():
    if len(sys.argv) < 6 or sys.argv[4] != "--":
        sys.exit(next(x for x in __doc__.splitlines() if x.startswith("Usage:")))
    log_path, platform, key_path, cmd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[5:]
    with open(key_path, encoding="utf-8") as f:
        pubkey = f.read().strip()
    bt = load_boot_test()
    cfg = config(platform)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    # Stopped (vm_halt, a kill): the command too, with TERM, which vm-run passes on to
    # its QEMU, and KILL if it is still there after 60 s; main's wait reaps it.
    stopped = []

    def stop(*_):
        stopped.append(True)
        proc.terminate()
        threading.Timer(60, proc.kill).start()
    signal.signal(signal.SIGTERM, stop)

    rate = cfg["rate"] if cfg["rate"] else bt.TYPING_RATE.get(platform)
    lines = shell_lines(pubkey, cfg)
    with open(log_path, "wb") as log:
        con = bt.Console(proc, log, rate)

        def console():
            while True:
                try:
                    con.expect(bt.MENU, timeout=3600)
                except TimeoutError:
                    continue
                except (EOFError, OSError, ValueError):
                    return
                try:
                    bootstrap(con, bt, lines)
                except (EOFError, OSError, ValueError):
                    return
        t = threading.Thread(target=console, daemon=True)
        t.start()
        rc = proc.wait()
        t.join(5)
    if stopped:
        os._exit(124)
    return rc


if __name__ == "__main__":
    sys.exit(main())
