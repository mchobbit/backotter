"""testvm/vm-run's machines (docs/design/arm64.md A1), with QEMU, uname, hostname, sudo, taskset and
swtpm stubbed and the firmware, sysfs and /proc paths moved into a temporary tree: the x86 machine
as before, the aarch64 one (virt, AAVMF, romfile=, tpm-tis-device, a SCSI CD, --bios and --q35
refused), the pinning to one kind of core, and the start again in the host's job_slice.

vm-run needs bash 4 or newer: under the Mac's 3.2 these are skipped (run them on a host)."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
T2 = os.path.dirname(HERE)
TESTVM = os.path.dirname(T2)
VM_RUN = os.path.join(TESTVM, "vm-run")


def bash_major():
    try:
        out = subprocess.run(["bash", "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


STUBS = {
    "qemu-img": '#!/bin/bash\ncase $1 in info) echo \'{"format": "qcow2"}\' ;; create) for a; do last=$a; done; : >"$last" ;;'
                ' map) echo "[]" ;; esac\n',
    "uname": '#!/bin/bash\n[[ ${1:-} == -m ]] && { echo "$FAKE_M"; exit 0; }\nexec /usr/bin/uname "$@"\n',
    "hostname": '#!/bin/bash\necho "$FAKE_HOST"\n',
    "swtpm": '#!/bin/bash\nwhile [[ $# -gt 0 ]]; do [[ $1 == --pid ]] && { sleep 30 </dev/null >/dev/null 2>&1 & '
             'echo $! >"${2#file=}"; }; shift; done\n',
    "taskset": '#!/bin/bash\necho "$1 $2" >"$W/taskset"\nshift 2\nexec "$@"\n',
    # sudo: its words to W/sudo, the scope's cgroup (unless FAKE_SCOPE_MISSES), then the job after setpriv's --
    "sudo": '#!/bin/bash\nprintf "%s\\n" "$@" >"$W/sudo"\n[[ -n ${FAKE_SCOPE_MISSES:-} ]] || '
            'echo "0::/bko.slice/run-p1.scope" >"$W/cgroup"\nwhile [[ $# -gt 0 && $1 != --reset-env ]]; do shift; done\n'
            'shift 2\nexec "$@"\n',
}
for q in ("qemu-system-x86_64", "qemu-system-aarch64"):
    STUBS[q] = f'#!/bin/bash\n{{ echo {q}; printf "%s\\n" "$@"; }} >"$W/qemu"\npwd >"$W/qemu.cwd"\n'


@unittest.skipUnless(bash_major() >= 4, "vm-run needs bash 4 or newer")
class VmRun(unittest.TestCase):
    def setUp(self):
        self.w = tempfile.mkdtemp(prefix="t2-vm-run-")
        self.addCleanup(shutil.rmtree, self.w, True)
        w = self.w
        for d in ("stub", "fw/OVMF", "fw/AAVMF", "vm/test2/t2lib", "imgs", "ram", "sys"):
            os.makedirs(os.path.join(w, d))
        with open(VM_RUN, encoding="utf-8") as f:
            code = f.read()
        for a, b in (("/usr/share/OVMF", f"{w}/fw/OVMF"), ("/usr/share/AAVMF", f"{w}/fw/AAVMF"),
                     ("/sys/devices/system/cpu", f"{w}/sys"), ("/proc/self/status", f"{w}/status"),
                     ("/proc/self/cgroup", f"{w}/cgroup")):
            self.assertIn(a, code)
            code = code.replace(a, b)
        self.vr = os.path.join(w, "vm", "vm-run")
        with open(self.vr, "w") as f:
            f.write(code)
        os.chmod(self.vr, 0o755)
        shutil.copy(os.path.join(T2, "t2lib", "hosts.py"), os.path.join(w, "vm", "test2", "t2lib", "hosts.py"))
        with open(os.path.join(TESTVM, "hosts.json")) as f:
            self.table = json.load(f)
        self.write_table()
        for n, text in STUBS.items():
            p = os.path.join(w, "stub", n)
            with open(p, "w") as f:
                f.write(text)
            os.chmod(p, 0o755)
        for f in ("OVMF/OVMF_CODE_4M.secboot.fd", "OVMF/OVMF_VARS_4M.ms.fd", "OVMF/OVMF_CODE_4M.fd", "OVMF/OVMF_VARS_4M.fd",
                  "AAVMF/AAVMF_CODE.ms.fd", "AAVMF/AAVMF_CODE.no-secboot.fd", "AAVMF/AAVMF_VARS.fd"):
            self.put(f"fw/{f}", f + "\n")
        self.put("fw/AAVMF/AAVMF_VARS.ms.fd", "ms vars\n")
        for i in ("a.qcow2", "b.qcow2", "cd.iso"):
            self.put(f"imgs/{i}", "")
        self.cores({c: 0xd81 for c in (0, 1, 6, 7, 8, 9, 10, 11)} | {c: 0xd80 for c in (2, 3, 4, 5)})
        self.put("status", "Name:\tvm-run\nCpus_allowed_list:\t0-11\n")
        self.put("cgroup", "0::/user.slice/user-1000.slice/session-1.scope\n")

    def put(self, rel, text):
        with open(os.path.join(self.w, rel), "w") as f:
            f.write(text)

    def get(self, rel):
        p = os.path.join(self.w, rel)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read()

    def cores(self, parts):
        for c, part in parts.items():
            d = os.path.join(self.w, "sys", f"cpu{c}", "regs", "identification")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "midr_el1"), "w") as f:
                f.write(f"0x00000000410f{part:03x}{1:01x}\n")

    def write_table(self):
        with open(os.path.join(self.w, "vm", "hosts.json"), "w") as f:
            json.dump(self.table, f)

    def run_vm(self, *args, m="aarch64", host="nosuchhost", env=None):
        e = {k: v for k, v in os.environ.items() if k not in ("TMPDIR", "BKO_HOSTS_JSON", "BKO_VM_RUN_SLICED")}
        e.update(PATH=os.path.join(self.w, "stub") + os.pathsep + e["PATH"], W=self.w, FAKE_M=m, FAKE_HOST=host,
                 RAM_DIR=os.path.join(self.w, "ram"), TMPDIR=os.path.join(self.w, "ram"), SSH_PORT="3201", RT_RAM="0",
                 **(env or {}))
        for f in ("qemu", "taskset", "sudo"):
            if os.path.exists(os.path.join(self.w, f)):
                os.remove(os.path.join(self.w, f))
        r = subprocess.run([self.vr] + list(args), capture_output=True, text=True, env=e, cwd=os.path.join(self.w, "imgs"),
                           stdin=subprocess.DEVNULL, timeout=60)
        q = self.get("qemu")
        return r, (q.splitlines() if q is not None else None)

    def opt(self, argv, name):
        """The values after each NAME in argv."""
        return [argv[i + 1] for i, a in enumerate(argv) if a == name]

    def test_x86_as_before(self):
        img = os.path.join(self.w, "imgs", "a.qcow2")
        r, q = self.run_vm("a.qcow2", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(q, ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-drive",
                             f"file={img},format=qcow2,if=virtio", "-nic",
                             "user,ipv6=off,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:3201-:22", "-nographic",
                             "-machine", "pc"])
        r, q = self.run_vm("--uefi", "--tpm", "tpm", "a.qcow2", m="x86_64")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("q35,smm=on", self.opt(q, "-machine"))
        self.assertIn(f"if=pflash,format=raw,unit=0,readonly=on,file={self.w}/fw/OVMF/OVMF_CODE_4M.secboot.fd",
                      self.opt(q, "-drive"))
        self.assertIn("tpm-tis,tpmdev=tpm0", self.opt(q, "-device"))
        self.assertIsNone(self.get("taskset"))
        r, _ = self.run_vm("--bios", "a.qcow2", m="x86_64")
        self.assertEqual((r.returncode, r.stderr), (1, "vm-run: unknown option --bios\n"))

    def test_aarch64_machine(self):
        img = os.path.join(self.w, "imgs", "a.qcow2")
        r, q = self.run_vm("a.qcow2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(q[0], "qemu-system-aarch64")
        self.assertEqual(q[1:9], ["-enable-kvm", "-cpu", "host", "-smp", "2", "-m", "2048", "-drive"])
        self.assertEqual(self.opt(q, "-machine"), ["virt,gic-version=host"])
        self.assertNotIn("-nic", q)
        self.assertEqual(self.opt(q, "-netdev"), ["user,id=net0,ipv6=off,hostfwd=tcp:127.0.0.1:3201-:22"])
        self.assertEqual(self.opt(q, "-device"), ["virtio-net-pci,netdev=net0,romfile="])
        d = self.opt(q, "-drive")
        self.assertEqual(d[0], f"file={img},format=qcow2,if=virtio")
        self.assertEqual(d[1], f"if=pflash,format=raw,unit=0,readonly=on,file={self.w}/fw/AAVMF/AAVMF_CODE.no-secboot.fd")
        self.assertRegex(d[2], r"^if=pflash,format=raw,unit=1,file=.*/vars\.fd$")
        self.assertNotIn("-vga", q)
        self.assertNotIn("-boot", q)

    def test_aarch64_secure_boot_only_with_uefi(self):
        for args, code in ((["--uefi"], "AAVMF_CODE.ms.fd"), (["--uefi", "--no-secure-boot"], "AAVMF_CODE.no-secboot.fd"),
                           (["--no-secure-boot"], "AAVMF_CODE.no-secboot.fd"), ([], "AAVMF_CODE.no-secboot.fd")):
            r, q = self.run_vm(*args, "a.qcow2")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"if=pflash,format=raw,unit=0,readonly=on,file={self.w}/fw/AAVMF/{code}", self.opt(q, "-drive"),
                          args)
            self.assertNotIn("smm", " ".join(q))
        r, q = self.run_vm("--uefi", "--uefi-vars", "kept.fd", "a.qcow2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get("imgs/kept.fd"), "ms vars\n")   # made from AAVMF_VARS.ms.fd
        self.assertIn("if=pflash,format=raw,unit=1,file=kept.fd", self.opt(q, "-drive"))

    def test_aarch64_refusals(self):
        for args, msg in ((["--bios"], "vm-run: --bios: an aarch64 host's VMs boot with UEFI (AAVMF) only: virt has no SeaBIOS"),
                          (["--q35"], "vm-run: --q35: an aarch64 host's VMs are QEMU's virt machine (UEFI), never q35"),
                          (["--uefi", "--q35"], "vm-run: --q35: an aarch64 host's VMs are QEMU's virt machine"),
                          (["--disk-ports", "5", "--disk-bus", "scsi"], "vm-run: --disk-ports needs virtio disks")):
            r, q = self.run_vm(*args, "a.qcow2")
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertIsNone(q)

    def test_aarch64_devices(self):
        r, q = self.run_vm("--tpm", "tpm", "--cdrom", "cd.iso", "--no-vga", "--nic", "e1000e", "--mac", "52:54:00:00:00:02",
                           "--disk-bus", "sata", "--disk", "b.qcow2", "--disk-ports", "-", "a.qcow2")
        self.assertEqual(r.returncode, 1)   # --disk-ports with sata
        r, q = self.run_vm("--tpm", "tpm", "--cdrom", "cd.iso", "--no-vga", "--nic", "e1000e", "--mac", "52:54:00:00:00:02",
                           "--disk-bus", "sata", "--disk", "b.qcow2", "a.qcow2")
        self.assertEqual(r.returncode, 0, r.stderr)
        dev = self.opt(q, "-device")
        self.assertIn("tpm-tis-device,tpmdev=tpm0", dev)
        self.assertIn("e1000e,netdev=net0,romfile=,mac=52:54:00:00:00:02", dev)
        self.assertIn("ich9-ahci,id=ahci0", dev)
        self.assertIn("ide-hd,drive=disk1,bus=ahci0.1", dev)
        self.assertIn("scsi-cd,drive=cd0,bus=scsicd.0,bootindex=0", dev)
        self.assertIn(f"if=none,id=cd0,file={self.w}/imgs/cd.iso,media=cdrom,readonly=on", self.opt(q, "-drive"))
        self.assertNotIn("-vga", q)
        self.assertNotIn("-boot", q)
        r, q = self.run_vm("--disk-ports", "5,-", "--disk", "b.qcow2", "a.qcow2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pcie-root-port,id=vmrp0,bus=pcie.0,chassis=20,addr=0x5,io-reserve=0", self.opt(q, "-device"))

    def test_pinning_to_one_kind_of_core(self):
        r, q = self.run_vm("a.qcow2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.get("taskset"), "-c 0,1,6,7,8,9,10,11\n")
        self.assertIn("vm-run: cores of 2 kinds (MIDR parts ", r.stderr)
        self.assertIn("QEMU pinned to part 0xd81's, CPUs 0,1,6,7,8,9,10,11", r.stderr)
        for allowed in ("0-1,6-11", "2-5", "3"):   # already one kind (bko.slice's AllowedCPUs): not pinned
            self.put("status", f"Cpus_allowed_list:\t{allowed}\n")
            r, q = self.run_vm("a.qcow2")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(self.get("taskset"), allowed)
            self.assertNotIn("pinned", r.stderr)
        self.put("status", "Cpus_allowed_list:\t1-3\n")   # 1 of d81, 2 of d80: the more numerous
        r, q = self.run_vm("a.qcow2")
        self.assertEqual(self.get("taskset"), "-c 2,3\n")
        self.put("status", "Cpus_allowed_list:\t0-11\n")
        self.cores({c: 0xd81 for c in range(12)})
        r, q = self.run_vm("a.qcow2")
        self.assertIsNone(self.get("taskset"))
        r, q = self.run_vm("a.qcow2", m="x86_64")   # never on x86
        self.assertIsNone(self.get("taskset"))

    def test_job_slice(self):
        row = next(r for r in self.table["hosts"] if r["name"] == "bigfoot")
        self.assertEqual(row["job_slice"], "bko.slice")
        user = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        r, q = self.run_vm("a.qcow2", host="bigfoot", env={"MEMORY": "3072"})
        self.assertEqual(r.returncode, 0, r.stderr)
        sudo = self.get("sudo").splitlines()
        self.assertEqual(sudo[:13], ["-n", "systemd-run", "--scope", "--quiet", "--slice=bko.slice", "--", "setpriv",
                                     f"--reuid={user}", f"--regid={user}", "--init-groups", "--reset-env", "--", "env"])
        self.assertEqual(sudo[13], "-i")
        self.assertIn("MEMORY=3072", sudo)
        self.assertIn("BKO_VM_RUN_SLICED=1", sudo)
        self.assertEqual(sudo[-2:], [self.vr, "a.qcow2"])
        self.assertIn("-m", q)
        self.assertEqual(self.opt(q, "-m"), ["3072"])      # its environment came along
        self.assertEqual(self.get("qemu.cwd").strip(), os.path.realpath(os.path.join(self.w, "imgs")))
        # Already in the slice: no sudo.
        r, q = self.run_vm("a.qcow2", host="bigfoot")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.get("sudo"))
        self.put("cgroup", "0::/user.slice/user-1000.slice/session-1.scope\n")
        # The scope didn't land in the slice: no loop, an error.
        r, q = self.run_vm("a.qcow2", host="bigfoot", env={"FAKE_SCOPE_MISSES": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("vm-run: started again in bko.slice, but /proc/self/cgroup says 0::/user.slice", r.stderr.replace(
            f"{self.w}/cgroup", "/proc/self/cgroup"))
        self.assertIsNone(q)
        # A host without a job_slice, or no row: as ever.
        for h in ("chopin", "nosuchhost"):
            r, q = self.run_vm("a.qcow2", host=h)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(self.get("sudo"), h)


if __name__ == "__main__":
    unittest.main()
