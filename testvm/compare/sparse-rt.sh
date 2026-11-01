#!/bin/sh
# Sparse files through restic-bko and rustic-bko (SPEC3 2 and 3): files with
# known layouts of holes, backed up with each tool and restored with each,
# in all four directions (restic->restic, rustic->rustic, and across).
# Per direction and file it reports and checks:
#   - the contents, identical (size and a digest of every non-zero 4 KiB
#     block with its offset; holes are read as zeros, not read);
#   - unix.sparse as the backing tool marked it, against S3's rule (sizes,
#     st_blocks and the SEEK_HOLE probe of the source);
#   - st_blocks of the source and the restored file, against SPEC3 3 (as
#     revised for round 9) for a file S3 marks: where the source's holes are
#     B-aligned (B: the destination's f_bsize, at least 4096), no more than
#     the source's; never more than the source's + one block per blob.
#     Both plus the metadata the restored file has beyond the source's
#     (st_blocks less its data extents rounded out to B: XFS and ext4 count
#     their extent-tree blocks in st_blocks, ZFS its indirect blocks), which
#     is reported.  A file S3 doesn't mark must come back dense (S5): not
#     sparse by S3's own rule (its st_blocks cover its size, or SEEK_HOLE
#     finds no hole).  Without SEEK_DATA maps (OpenBSD, NetBSD, DragonFly)
#     there is neither alignment nor metadata to go by: the bound with one
#     block per blob alone;
#   - the SEEK_DATA/SEEK_HOLE extent maps of both (not a check), in full
#     where they are short;
# the wall time of each restore and of the sync after it (not a check); and
# per tool, the bytes read and the wall time of a backup of the whole
# tree and of the big file alone (read from the disk: /proc/PID/io
# read_bytes, through read(): rchar, where there is /proc/PID/io (Linux);
# elsewhere only the times and getrusage's block input operations, which
# ZFS doesn't count).  The page cache is emptied of the source files first
# (posix_fadvise DONTNEED; on SPARSE_FS=zfs the pool is exported and
# imported again, which empties the ARC of it; in a directory of a ZFS that
# is there, the ARC keeps them, read_bytes says little and rchar is the
# figure).  On ext4 and XFS read_bytes is about the
# data alone even when the holes are read (a hole needs no disk read): it is
# rchar that shows whether they were.
#
# A socket in the tree, which both tools skip (SPEC3 1): no node for it in
# either tool's snapshot, nothing in its place after any restore, no word
# about it from a backup or a restore, exit 0.
#
# SPEC3 2 across the tools: the tree backed up with restic into rustic's
# repository too, and every file's content blob IDs the same as rustic's
# (rustic_core 9f88530 made rustic's chunker cut where restic's does).
#
# With OLD_RESTIC and OLD_RUSTIC (an earlier build of each tool), also the
# snapshot identity of SPEC3 2: the same tree backed up with the old binary
# into the repository the new one made (--force: every file read again),
# and for every file the content blob IDs of the two snapshots must be the
# same (but a rustic older than 9f88530 may cut a file differently, where
# the new rustic's are restic's); and the old binaries' backups of the big file are measured too, for
# before vs after, as are their restores of the new binaries' snapshots
# (each tool's own): every file's st_blocks beside the new restore's, and
# the times (not a check).
#
# Usage: sparse-rt.sh RESTIC RUSTIC [OLD_RESTIC OLD_RUSTIC]
#   as root.  On Linux it makes the file system to test itself (SPARSE_FS);
#   elsewhere (a BSD or illumos guest: guest-run.sh with this script,
#   rt-common.sh and the binaries) it works in a directory (SPARSE_DIR) of
#   the file system that is there.  Needs cc (or gcc, clang) for a small
#   helper, which it builds, or else python3 (the same in Python); dd,
#   truncate (else dd does its work), awk.  netbsd11 and alpinebsd have
#   no compiler or python3: NetBSD's build image (VM_IMAGE=
#   ~/backotter-work/vms-bko-build/netbsd11-rust.qcow2) has cc.
#
# Environment:
#   SPARSE_FS   Linux: ext4 (default), xfs, btrfs, zfs (a pool on a file;
#               SPARSE_POOL names it, default PREFIX-RUN), tmpfs, or dir
#               (SPARSE_DIR as it is); elsewhere dir, the default
#   SPARSE_ZFS_OPTS
#               more zpool create options for zfs (e.g. "-O compression=off")
#   SPARSE_DIR  the directory for dir (default /root/bko-sparse)
#   SPARSE_RUN  this run's name (default SPARSE_FS): its work directory,
#               mount point and pool are its own, so that runs of other
#               names can go at the same time
#   SPARSE_PREFIX
#               the names' prefix (default bko-sparse): mount point
#               /mnt/PREFIX/RUN, pool PREFIX-RUN, work directory
#               /var/tmp/PREFIX/RUN; a session of its own takes its own
#               (e.g. bkor9-sparse)
#   SPARSE_WORK work directory: repositories, image, results (default
#               /var/tmp/PREFIX/RUN on Linux, /root/bko-sparse-work
#               elsewhere); maps, digests, decoded snapshots and the
#               measurements in its out/
#   IMG_GB      size of the image or tmpfs (default 16; files hold ~1.5 GiB)
#   BIG         size of the big file in GiB (default 20; 0: none): 100
#               extents of 2 MiB random data spread over it, the last one
#               ending at EOF
#   BY_CONTENT  set: restore with --sparse (restic) and --sparse by-content
#               (rustic): every file restored sparse, not only marked ones
#   KEEP        set: leave the file system, pool and repositories
#   HELPER      python: the Python helper even where there is a C compiler
#
# The report ends with "sparse-rt: PASS" or "sparse-rt: FAIL: what failed".

set -u
die() { echo "sparse-rt: $*" >&2; exit 2; }
[ $# -eq 2 ] || [ $# -eq 4 ] || { sed -n '2,86p' "$0" >&2; exit 2; }
uid=$(id -u 2>/dev/null) || uid=$(/usr/xpg4/bin/id -u)   # illumos' id has no -u
[ "$uid" = 0 ] || die "run it as root"
abspath() { case $1 in /*) echo "$1" ;; *) echo "$(pwd)/$1" ;; esac; }
for b in "$@"; do [ -x "$b" ] || die "no $b"; done
RESTIC=$(abspath "$1") RUSTIC=$(abspath "$2")
OLD_RESTIC="" OLD_RUSTIC=""
[ $# -eq 4 ] && OLD_RESTIC=$(abspath "$3") OLD_RUSTIC=$(abspath "$4")
here=$(cd "$(dirname "$0")" && pwd)
os=$(uname -s)
# illumos: GNU truncate, grep and awk before the system's (as nonroot-inner.sh).
[ "$os" = SunOS ] && PATH=/usr/gnu/bin:/usr/bin:/usr/sbin:$PATH
if [ "$os" = Linux ]; then FS=${SPARSE_FS:-ext4}; else FS=${SPARSE_FS:-dir}; fi
RUN=${SPARSE_RUN:-$FS} PFX=${SPARSE_PREFIX:-bko-sparse}
if [ "$os" = Linux ]; then W=${SPARSE_WORK:-/var/tmp/$PFX/$RUN}; else W=${SPARSE_WORK:-/root/bko-sparse-work}; fi
if [ "$FS" = dir ]; then MP=${SPARSE_DIR:-/root/bko-sparse}; else MP=/mnt/$PFX/$RUN; fi
POOL=${SPARSE_POOL:-$PFX-$RUN}
BIG=${BIG:-20} IMG_GB=${IMG_GB:-16}
OUT=$W/out SRC=$MP/src
case $FS in ext4|xfs|btrfs|zfs|tmpfs) [ "$os" = Linux ] || die "SPARSE_FS=$FS is for Linux" ;;
    dir) ;; *) die "no such SPARSE_FS: $FS" ;; esac
mkdir -p "$W" || die "can't make $W"
exec 9>"$W/lock"
# On Linux (a host, shared): a guest is a throwaway copy, and NetBSD's
# flock(1) fails "-n 9" there as well.
if [ "$os" = Linux ] && command -v flock >/dev/null 2>&1; then
    flock -n 9 || die "another run of $RUN is going ($W/lock)"
fi
# NetBSD has no truncate(1): dd sizes a file to its seek offset all the same.
command -v truncate >/dev/null 2>&1 || truncate() {  # -s SIZE FILE
    dd if=/dev/null of="$3" bs=1 seek="$2" count=0 2>/dev/null
}
rm -rf "$OUT" "$W/tmp" "$W"/repo-* && mkdir -p "$OUT" "$W/tmp" || die "can't clean $W"
export RT_TMP=$W/tmp
# shellcheck source=rt-common.sh
. "$here/rt-common.sh"
export RESTIC_PASSWORD=backotter-compare RUSTIC_PASSWORD=backotter-compare

# The helper: extent maps, digests, cache eviction, preallocation, and a
# command's bytes read and time.
H=$W/tmp/bko-sparse-helper
cat >$H.c <<'C'
#if defined(__linux__)
#define _GNU_SOURCE
#endif
#if defined(__sun)
#define __EXTENSIONS__
#endif
#include <sys/types.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *ename(int e) {
	switch (e) {
	case EINVAL: return "EINVAL";
	case ENXIO: return "ENXIO";
#if defined(ENOTSUP) && ENOTSUP != EOPNOTSUPP
	case ENOTSUP: return "ENOTSUP";
#endif
	case EOPNOTSUPP: return "EOPNOTSUPP";
	case ENOTTY: return "ENOTTY";
	default: return strerror(e);
	}
}

static int openr(const char *p, struct stat *st) {
	int fd = open(p, O_RDONLY);

	if (fd < 0 || fstat(fd, st) != 0) {
		perror(p);
		exit(1);
	}
	return fd;
}

/* size, st_blocks, st_blksize, f_bsize, the SEEK_HOLE probe at 0, and the
 * data extents ("start-end" byte ranges, end exclusive). */
static int map(const char *p) {
	struct stat st;
	struct statvfs sv;
	int fd = openr(p, &st);
	unsigned long fsb = fstatvfs(fd, &sv) == 0 ? (unsigned long)sv.f_bsize : 0;

	printf("size=%lld blocks=%lld blksize=%ld fsbsize=%lu", (long long)st.st_size,
	    (long long)st.st_blocks, (long)st.st_blksize, fsb);
#if defined(SEEK_HOLE) && defined(SEEK_DATA)
	{
		off_t h = lseek(fd, 0, SEEK_HOLE), off, d, e;
		long long n = 0, data = 0;
		int pass, err = 0;

		if (h < 0)
			printf(" hole0=err:%s", ename(errno));
		else
			printf(" hole0=%lld", (long long)h);
		for (pass = 0; pass < 2 && !err; pass++) {
			if (pass == 1)
				printf(" nextents=%lld data=%lld extents=", n, data);
			for (off = 0; off < st.st_size; off = e) {
				if ((d = lseek(fd, off, SEEK_DATA)) < 0) {
					if (errno != ENXIO) {
						printf(" extents=err:%s", ename(errno));
						err = 1;
					}
					break;
				}
				if ((e = lseek(fd, d, SEEK_HOLE)) < 0) {
					printf(" extents=err:%s", ename(errno));
					err = 1;
					break;
				}
				if (pass == 0) {
					n++;
					data += e - d;
				} else {
					printf("%s%lld-%lld", off ? "," : "", (long long)d, (long long)e);
				}
			}
		}
	}
#else
	printf(" hole0=none nextents=none data=none extents=none");
#endif
	printf("\n");
	return 0;
}

/* size, and FNV-1a over (offset, bytes) of every non-zero 4 KiB block, in
 * order: equal for equal contents, whatever is a hole.  Holes found by
 * SEEK_DATA are not read. */
static int digest(const char *p) {
	struct stat st;
	int fd = openr(p, &st);
	static unsigned char buf[1 << 20];
	uint64_t hsh = 1469598103934665603ULL;
	off_t next = 0, d, e, a;
	ssize_t r;
	int i, z;

	while (next < st.st_size) {
		d = next;
		e = st.st_size;
#if defined(SEEK_HOLE) && defined(SEEK_DATA)
		if ((d = lseek(fd, next, SEEK_DATA)) < 0) {
			if (errno == ENXIO)
				break;
			d = next;
		} else if ((e = lseek(fd, d, SEEK_HOLE)) < 0) {
			e = st.st_size;
		}
#endif
		d -= d % 4096;
		if (d < next)
			d = next;
		if (e % 4096)
			e += 4096 - e % 4096;
		if (e > st.st_size)
			e = st.st_size;
		for (a = d; a < e; a += r) {
			size_t want = e - a < (off_t)sizeof(buf) ? (size_t)(e - a) : sizeof(buf);

			if ((r = pread(fd, buf, want, a)) <= 0) {
				perror(p);
				return 1;
			}
			for (i = 0; i < r; i += 4096) {
				int len = r - i < 4096 ? (int)(r - i) : 4096, k;
				uint64_t o = (uint64_t)(a + i);

				for (z = 1, k = 0; k < len; k++)
					if (buf[i + k]) {
						z = 0;
						break;
					}
				if (z)
					continue;
				for (k = 0; k < 8; k++)
					hsh = (hsh ^ ((o >> (8 * k)) & 0xff)) * 1099511628211ULL;
				for (k = 0; k < len; k++)
					hsh = (hsh ^ buf[i + k]) * 1099511628211ULL;
			}
		}
		next = e;
	}
	printf("size=%lld digest=%016llx\n", (long long)st.st_size, (unsigned long long)hsh);
	return 0;
}

static int evict(const char *p) {
	struct stat st;
	int fd = openr(p, &st);

	fsync(fd);
#ifdef POSIX_FADV_DONTNEED
	posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
#endif
	return 0;
}

static int falloc(const char *p, long long len) {
#if defined(__linux__) || defined(__FreeBSD__) || defined(__sun)
	int fd = open(p, O_RDWR | O_CREAT | O_EXCL, 0644), e;

	if (fd < 0) {
		perror(p);
		return 1;
	}
	if ((e = posix_fallocate(fd, 0, len)) != 0) {
		fprintf(stderr, "%s: posix_fallocate: %s\n", p, ename(e));
		unlink(p);
		return 1;
	}
	return 0;
#else
	fprintf(stderr, "%s: no posix_fallocate here\n", p);
	return 1;
#endif
}

/* Run a command; into OUT: wall time, CPU, block input operations, and on
 * Linux /proc/PID/io's rchar and read_bytes, read while the child is a
 * zombie (waitid WNOWAIT), so that all of its reading is in them. */
static int run(const char *out, char **argv) {
	struct timespec t0, t1;
	struct rusage ru;
	char rchar[32] = "n/a", rbytes[32] = "n/a";
	pid_t pid;
	int st;
	FILE *f;

	clock_gettime(CLOCK_MONOTONIC, &t0);
	if ((pid = fork()) < 0) {
		perror("fork");
		return 1;
	}
	if (pid == 0) {
		execvp(argv[0], argv);
		perror(argv[0]);
		_exit(127);
	}
#ifdef __linux__
	{
		siginfo_t si;
		char path[64], line[128];

		while (waitid(P_PID, pid, &si, WEXITED | WNOWAIT) != 0 && errno == EINTR)
			;
		snprintf(path, sizeof(path), "/proc/%d/io", (int)pid);
		if ((f = fopen(path, "r")) != NULL) {
			while (fgets(line, sizeof(line), f)) {
				sscanf(line, "rchar: %31s", rchar);
				sscanf(line, "read_bytes: %31s", rbytes);
			}
			fclose(f);
		}
	}
#endif
	while (waitpid(pid, &st, 0) < 0 && errno == EINTR)
		;
	clock_gettime(CLOCK_MONOTONIC, &t1);
	getrusage(RUSAGE_CHILDREN, &ru);
	if ((f = fopen(out, "w")) == NULL) {
		perror(out);
		return 1;
	}
	fprintf(f, "wall=%.2f user=%ld.%02ld sys=%ld.%02ld inblock=%ld rchar=%s read_bytes=%s exit=%d\n",
	    (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9,
	    (long)ru.ru_utime.tv_sec, (long)ru.ru_utime.tv_usec / 10000,
	    (long)ru.ru_stime.tv_sec, (long)ru.ru_stime.tv_usec / 10000,
	    (long)ru.ru_inblock, rchar, rbytes,
	    WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st));
	fclose(f);
	return WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st);
}

int main(int argc, char **argv) {
	if (argc == 3 && strcmp(argv[1], "map") == 0)
		return map(argv[2]);
	if (argc == 3 && strcmp(argv[1], "digest") == 0)
		return digest(argv[2]);
	if (argc == 3 && strcmp(argv[1], "evict") == 0)
		return evict(argv[2]);
	if (argc == 4 && strcmp(argv[1], "falloc") == 0)
		return falloc(argv[2], atoll(argv[3]));
	if (argc > 3 && strcmp(argv[1], "run") == 0)
		return run(argv[2], argv + 3);
	fprintf(stderr, "usage: helper map|digest|evict FILE | falloc FILE LEN | run OUT CMD...\n");
	return 2;
}
C
# The same in Python, where there is no C compiler (omnios58 has python3 and
# no gcc): the digests are SHA-256 rather than FNV-1a, and are compared only
# with each other.
cat >$H.py <<'PY'
import errno, hashlib, os, resource, subprocess, sys, time

NAMES = {errno.EINVAL: "EINVAL", errno.ENXIO: "ENXIO", errno.EOPNOTSUPP: "EOPNOTSUPP", errno.ENOTTY: "ENOTTY"}
if getattr(errno, "ENOTSUP", errno.EOPNOTSUPP) != errno.EOPNOTSUPP:
    NAMES[errno.ENOTSUP] = "ENOTSUP"


def ename(e):
    return NAMES.get(e, os.strerror(e))


def extents(fd, size):
    """The data extents, or the error that stopped the walk."""
    out, off = [], 0
    while off < size:
        try:
            d = os.lseek(fd, off, os.SEEK_DATA)
        except OSError as e:
            return out, (None if e.errno == errno.ENXIO else ename(e.errno))
        try:
            off = os.lseek(fd, d, os.SEEK_HOLE)
        except OSError as e:
            return out, ename(e.errno)
        out.append((d, off))
    return out, None


def fmap(p):
    fd = os.open(p, os.O_RDONLY)
    st = os.fstat(fd)
    try:
        fsb = os.fstatvfs(fd).f_bsize
    except OSError:
        fsb = 0
    line = "size=%d blocks=%d blksize=%d fsbsize=%d" % (st.st_size, st.st_blocks, st.st_blksize, fsb)
    if hasattr(os, "SEEK_DATA"):
        try:
            line += " hole0=%d" % os.lseek(fd, 0, os.SEEK_HOLE)
        except OSError as e:
            line += " hole0=err:" + ename(e.errno)
        ext, err = extents(fd, st.st_size)
        if err:
            line += " extents=err:" + err
        else:
            line += " nextents=%d data=%d extents=%s" % (len(ext), sum(e - d for d, e in ext),
                                                        ",".join("%d-%d" % x for x in ext))
    else:
        line += " hole0=none nextents=none data=none extents=none"
    print(line)


def digest(p):
    fd = os.open(p, os.O_RDONLY)
    size = os.fstat(fd).st_size
    ranges = [(0, size)]
    if hasattr(os, "SEEK_DATA"):
        ext, err = extents(fd, size)
        if not err:
            ranges = ext
    h, nxt = hashlib.sha256(), 0
    for d, e in ranges:
        d = max(d - d % 4096, nxt)
        e = min(e + (-e) % 4096, size)
        a = d
        while a < e:
            buf = os.pread(fd, min(e - a, 1 << 20), a)
            if not buf:
                sys.exit("%s: short read" % p)
            for i in range(0, len(buf), 4096):
                b = buf[i:i + 4096]
                if b.count(0) != len(b):
                    h.update((a + i).to_bytes(8, "little") + b)
            a += len(buf)
        nxt = max(nxt, e)
    print("size=%d digest=%s" % (size, h.hexdigest()[:16]))


def evict(p):
    fd = os.open(p, os.O_RDONLY)
    os.fsync(fd)
    if hasattr(os, "posix_fadvise"):
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def falloc(p, n):
    if not hasattr(os, "posix_fallocate"):
        sys.exit("%s: no posix_fallocate here" % p)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.posix_fallocate(fd, 0, n)
    except OSError as e:
        os.unlink(p)
        sys.exit("%s: posix_fallocate: %s" % (p, ename(e.errno)))


def run(out, argv):
    t0 = time.monotonic()
    pr = subprocess.Popen(argv)
    rchar = rbytes = "n/a"
    if sys.platform.startswith("linux") and hasattr(os, "waitid"):
        os.waitid(os.P_PID, pr.pid, os.WEXITED | os.WNOWAIT)
        try:
            for l in open("/proc/%d/io" % pr.pid):
                k, _, v = l.partition(":")
                if k == "rchar":
                    rchar = v.strip()
                elif k == "read_bytes":
                    rbytes = v.strip()
        except OSError:
            pass
    st = pr.wait()
    t1 = time.monotonic()
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    code = st if st >= 0 else 128 - st
    with open(out, "w") as f:
        f.write("wall=%.2f user=%.2f sys=%.2f inblock=%d rchar=%s read_bytes=%s exit=%d\n"
                % (t1 - t0, ru.ru_utime, ru.ru_stime, ru.ru_inblock, rchar, rbytes, code))
    return code


a = sys.argv[1:]
if len(a) == 2 and a[0] == "map":
    fmap(a[1])
elif len(a) == 2 and a[0] == "digest":
    digest(a[1])
elif len(a) == 2 and a[0] == "evict":
    evict(a[1])
elif len(a) == 3 and a[0] == "falloc":
    falloc(a[1], int(a[2]))
elif len(a) > 2 and a[0] == "run":
    sys.exit(run(a[1], a[2:]))
else:
    sys.exit("usage: helper map|digest|evict FILE | falloc FILE LEN | run OUT CMD...")
PY
cc=""
for c in cc gcc clang; do command -v $c >/dev/null 2>&1 && { cc=$c; break; }; done
if [ -n "$cc" ] && [ "${HELPER:-}" != python ]; then
    $cc -O2 -o $H $H.c || { echo "sparse-rt: FAIL: can't build the helper"; exit 1; }
elif command -v python3 >/dev/null 2>&1; then
    H="python3 -I $H.py"
else
    echo "sparse-rt: FAIL: no cc, gcc, clang or python3 for the helper"; exit 1
fi

# The file system.
cleanup() {
    if [ -z "${KEEP:-}" ]; then
        cd /
        case $FS in
            zfs) zpool destroy "$POOL" 2>/dev/null ;;
            dir) rm -rf "$MP/src" "$MP"/dst-* ;;
            *) umount "$MP" 2>/dev/null ;;
        esac
        [ "$FS" = dir ] || rmdir "$MP" 2>/dev/null
        rm -f "$W/fs.img"; rm -rf "$W"/repo-*
    fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM
mkdir -p "$MP"
if [ "$FS" != dir ]; then
    if [ "$FS" = zfs ]; then zpool list "$POOL" >/dev/null 2>&1 && zpool destroy "$POOL"
    else umount "$MP" 2>/dev/null; fi
    rm -f "$W/fs.img"
    case $FS in
        tmpfs) mount -t tmpfs -o size=${IMG_GB}g tmpfs "$MP" ;;
        # cachefile=none: no fsync of /etc/zfs/zpool.cache at every create
        # and import, which waits for the root file system's journal (round
        # 10: minutes, on a host others kept busy).
        zfs) truncate -s ${IMG_GB}G "$W/fs.img" &&
                 zpool create -f -o ashift=12 -o cachefile=none -O mountpoint="$MP" -O atime=off ${SPARSE_ZFS_OPTS:-} "$POOL" "$W/fs.img" ;;
        *) truncate -s ${IMG_GB}G "$W/fs.img" && case $FS in
               # Inode tables and journal now, not by a kernel thread
               # during the measured backups.
               ext4) mkfs.ext4 -q -F -E lazy_itable_init=0,lazy_journal_init=0 "$W/fs.img" ;;
               xfs) mkfs.xfs -q -f "$W/fs.img" ;;
               btrfs) mkfs.btrfs -q -f "$W/fs.img" ;;
           esac && mount -o loop "$W/fs.img" "$MP" ;;
    esac || { echo "sparse-rt: FAIL: can't make a $FS file system at $MP"; exit 1; }
fi
rm -rf "$MP/src" "$MP"/dst-*
mkdir -p "$SRC" || { echo "sparse-rt: FAIL: can't make $SRC"; exit 1; }
# ZFS counts a file's blocks in st_blocks only once the transaction group
# that wrote them is on disk, which sync(2) doesn't wait for on FreeBSD
# (round 9: the big file's source showed 137392 KiB of its 204800 KiB of
# data): settle waits for it too (zpool sync of the pool that holds MP),
# before any st_blocks below is read.
settle_pool=""
if [ "$FS" = zfs ]; then settle_pool=$POOL
elif command -v zfs >/dev/null 2>&1; then settle_pool=$(zfs list -H -o name "$MP" 2>/dev/null | sed 's|/.*||'); fi
# On a shared host sync(2) waits for every other file system's writes too
# (round 10: over 20 minutes behind other sessions' runs): on Linux
# syncfs(2) of this file system alone (GNU sync -f), here and in the
# measured sync after each restore.
if [ "$os" = Linux ] && sync -f "$MP" 2>/dev/null; then syncmd="sync -f $MP"; else syncmd=sync; fi
settle() { $syncmd; [ -z "$settle_pool" ] || zpool sync "$settle_pool"; }
echo "== $os $(uname -r), $FS at $MP$( [ "$FS" = zfs ] && echo " (pool $POOL, compression $(zfs get -H -o value compression "$POOL"))")"
echo "   restic: $RESTIC ($($RESTIC version 2>&1 | head -n 1))"
echo "   rustic: $RUSTIC ($($RUSTIC --version 2>&1 | head -n 1))"
[ -n "$OLD_RESTIC" ] && echo "   old restic: $OLD_RESTIC ($($OLD_RESTIC version 2>&1 | head -n 1))" &&
    echo "   old rustic: $OLD_RUSTIC ($($OLD_RUSTIC --version 2>&1 | head -n 1))"

# The files.  put FILE BLOCK COUNT: COUNT 4 KiB blocks of random data at
# block BLOCK.
put() { dd if=/dev/urandom of="$1" bs=4096 seek=$2 count=$3 conv=notrunc 2>/dev/null; }
M=256    # blocks in a MiB
cd "$SRC" || exit 1
# Holes at the start (data up to EOF), in the middle, at the end.
truncate -s 8388608 hole-start; put hole-start $((4 * M)) $((4 * M))
put hole-middle 0 $M; put hole-middle $((7 * M)) $M
put hole-end 0 $M; truncate -s 8388608 hole-end
# A hole and then 100 bytes of data at EOF, in a block of their own.
truncate -s 8388708 tail-at-eof
dd if=/dev/urandom of=tail-at-eof bs=1 seek=8388608 count=100 conv=notrunc 2>/dev/null
# 4 KiB data and 4 KiB holes, 256 of each; the same with 64 KiB.
i=0; while [ $i -lt 256 ]; do put holes-4k $((2 * i)) 1; i=$((i + 1)); done
truncate -s $((512 * 4096)) holes-4k
i=0; while [ $i -lt 32 ]; do put holes-64k $((32 * i)) 16; i=$((i + 1)); done
truncate -s $((64 * 64 * 1024)) holes-64k
# Holes of 1 and 3 blocks, 1 MiB and more, and a hole at the end: 48 MiB.
put mixed 0 300; put mixed 301 10; put mixed 314 6; put mixed 1000 2000; put mixed 5000 1
put mixed 5003 7; put mixed 9000 256; truncate -s $((48 * 1048576)) mixed
# Data inside one blob, holes on both sides.
truncate -s 4194304 one-blob; put one-blob 384 2
# All hole; all data; written zeros; preallocated (posix_fallocate: on Linux
# unwritten extents, which SEEK_HOLE takes for holes but st_blocks counts:
# not marked, S3 1).
truncate -s 67108864 all-hole
put full 0 $((8 * M))
dd if=/dev/zero of=zeros-written bs=4096 count=$((8 * M)) 2>/dev/null
$H falloc fallocated 16777216 || echo "   (no fallocated file here)"
# The big file: BIG GiB, 100 extents of 2 MiB, the last ending at EOF.
if [ "$BIG" -gt 0 ]; then
    truncate -s $((BIG * 1073741824)) big
    i=0
    while [ $i -lt 100 ]; do
        dd if=/dev/urandom of=big bs=1048576 seek=$((i * (BIG * 1024 - 2) / 99)) count=2 conv=notrunc 2>/dev/null
        i=$((i + 1))
    done
fi
cd /
settle
files=$(cd "$SRC" && ls)
# The socket, after the list of files: it has no map or digest.
if rt_mksock "$SRC/socket"; then sock=1; else sock=""; echo "   (no socket: nothing here could make one)"; fi

field() {  # KEY LINE: KEY's value in a helper line
    printf '%s\n' "$2" | tr ' ' '\n' | sed -n "s/^$1=//p"
}
# The source: maps and digests.
for f in $files; do
    $H map "$SRC/$f" >"$OUT/map.src.$f"
    $H digest "$SRC/$f" >"$OUT/digest.src.$f"
done
# S3's mark for each source file, from its lstat and probe; s3 for any
# helper map line (a restored file's too: dense is what S3 doesn't mark).
case $os in Linux|FreeBSD|Darwin|SunOS) probe=1 ;; *) probe="" ;; esac
expect_mark() { s3 "$(cat "$OUT/map.src.$1")"; }  # FILE
s3() {  # MAP-LINE
    m=$1
    sz=$(field size "$m") bl=$(field blocks "$m") h=$(field hole0 "$m")
    [ "$sz" -gt 0 ] && [ $((bl * 512)) -lt "$sz" ] || { echo no; return; }
    [ -n "$probe" ] || { echo yes; return; }
    case $h in
        err:EINVAL|err:ENOTSUP|err:EOPNOTSUPP|err:ENOTTY) echo yes ;;
        err:*|none) echo no ;;
        *) [ "$h" -lt "$sz" ] && echo yes || echo no ;;
    esac
}
echo "== the source (KiB allocated, size; extents by SEEK_DATA/SEEK_HOLE)"
for f in $files; do
    m=$(cat "$OUT/map.src.$f")
    printf '  %-14s %9s KiB of %12s  hole0 %-12s %5s extents, %s data bytes; S3 mark: %s\n' "$f" \
        $(($(field blocks "$m") / 2)) "$(field size "$m")" "$(field hole0 "$m")" "$(field nextents "$m")" \
        "$(field data "$m")" "$(expect_mark $f)"
done

failed=""
fail() { failed="$failed; $1"; }
evict_src() {
    $syncmd; for f in $files; do $H evict "$SRC/$f"; done
    if [ "$FS" = zfs ]; then
        zpool export "$POOL" && zpool import -o cachefile=none -d "$W" "$POOL" || { echo "can't export and import $POOL"; fail "ZFS eviction"; }
    fi
}
# BIN REPO: init REPO.
init() {
    if rt_is_rustic $1; then $1 --no-progress --no-cache -r $2 init >/dev/null 2>$W/tmp/err
    else $1 -r $2 --no-cache init -q 2>$W/tmp/err; fi
    s=$?; grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $W/tmp/err; return $s
}
# LABEL BIN REPO ARGS...: a backup, measured into $OUT/io.LABEL.
backup() {
    l=$1 b=$2 r=$3; shift 3
    if rt_is_rustic $b; then
        $H run "$OUT/io.$l" $b --no-progress --no-cache -r $r backup --set-atime yes "$@" >/dev/null 2>$W/tmp/err
    else
        $H run "$OUT/io.$l" $b -r $r --no-cache backup -q "$@" >/dev/null 2>$W/tmp/err
    fi
    s=$?; grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $W/tmp/err; return $s
}
# LABEL BIN REPO TARGET: a restore of the latest snapshot of the source,
# measured into $OUT/io.LABEL.
restore() {
    l=$1; shift
    if rt_is_rustic $1; then
        $H run "$OUT/io.$l" $1 --no-progress --no-cache -r $2 restore --numeric-id ${BY_CONTENT:+--sparse by-content} \
            "latest:$SRC" $3 >/dev/null 2>$W/tmp/err
    else
        $H run "$OUT/io.$l" $1 -r $2 --no-cache restore -q ${BY_CONTENT:+--sparse} "latest:$SRC" --target $3 2>$W/tmp/err
    fi
    s=$?; grep -v -e '^\[INFO\]' -e 'failed to get system time zone' $W/tmp/err; return $s
}
sockwarn() {  # WHAT: whether WHAT's stderr ($W/tmp/err) speaks of the socket
    [ -n "$sock" ] || return 0
    grep -i sock $W/tmp/err >/dev/null || return 0
    echo "socket: $1 warned about it (SPEC3 1: skipped silently)"; fail "$1 warned about the socket"
}
showio() {  # LABEL
    printf '  %-22s %s\n' "$1" "$(cat "$OUT/io.$1" 2>/dev/null || echo "(not run)")"
}

# Backups of the tree, one repository per tool.
for t in restic rustic; do
    if [ $t = restic ]; then b=$RESTIC; else b=$RUSTIC; fi
    echo "== backup with $t"
    init $b $W/repo-$t || { fail "init with $t"; continue; }
    evict_src
    backup tree.$t $b $W/repo-$t "$SRC"; s=$?
    echo "exit $s"; [ $s -eq 0 ] || fail "backup with $t"
    sockwarn "backup with $t"
    RT_CONTENT=1 rt_nodes "$RESTIC" $W/repo-$t "$SRC" >"$OUT/nodes.$t" ||
        { echo "can't decode the snapshot"; fail "decoding $t's snapshot"; }
    if [ -n "$sock" ] && [ -f "$OUT/nodes.$t" ]; then
        if $rt_awk -F '\t' '$1 == "socket" || /\ttype="socket"/' "$OUT/nodes.$t" | grep -q .; then
            echo "socket: IN THE SNAPSHOT (SPEC3 1: skipped)"; fail "$t's snapshot has the socket"
        else
            echo "socket: not in the snapshot"
        fi
    fi
done
node() { $rt_awk -F '\t' -v n="$2" '$1 == n' "$OUT/nodes.$1"; }  # TOOL FILE
blobs() { printf '%s\n' "$1" | tr '\t' '\n' | sed -n 's/^content=//p' | tr ',' '\n' | grep -c '[0-9a-f]'; }
# MAP-LINE B: the bytes of its data extents, each rounded out to B, and
# whether every one starts and ends (but at EOF) on a B boundary: "yes" or
# "no"; nothing without a map (no SEEK_DATA, or it failed).
extalloc() {
    case $(field nextents "$1") in ''|*[!0-9]*) return ;; esac
    field extents "$1" | tr ',' '\n' | $rt_awk -F- -v b=$2 -v size="$(field size "$1")" '
        NF == 2 { d = $1 + 0; e = $2 + 0; z = e; if (z % b) z += b - z % b
                  tot += z - (d - d % b); if (d % b || (e % b && e != size + 0)) mis++ }
        END { printf "%.0f %s\n", tot, (mis ? "no" : "yes") }'
}

# The four directions.
for d in restic-restic rustic-rustic restic-rustic rustic-restic; do
    bt=${d%-*} rt=${d#*-}
    if [ $rt = restic ]; then rb=$RESTIC; else rb=$RUSTIC; fi
    dst=$MP/dst-$d
    echo "== restore $bt->$rt${BY_CONTENT:+ (by content)}"
    [ -f "$OUT/nodes.$bt" ] || { echo "no snapshot"; fail "$bt->$rt: no snapshot"; continue; }
    restore restore.$d $rb $W/repo-$bt $dst; s=$?
    $H run "$OUT/io.sync.$d" $syncmd
    settle
    echo "exit $s (wall $(field wall "$(cat "$OUT/io.restore.$d" 2>/dev/null)") s, the sync after it $(field wall "$(cat "$OUT/io.sync.$d" 2>/dev/null)") s)"
    [ $s -eq 0 ] || fail "restore $bt->$rt"
    sockwarn "restore $bt->$rt"
    if [ -n "$sock" ]; then
        if [ -e "$dst/socket" ] || [ -h "$dst/socket" ]; then
            echo "socket: RESTORED (SPEC3 1: skipped)"; fail "$bt->$rt restored the socket"
        else
            echo "socket: not restored"
        fi
    fi
    echo "== $bt->$rt per file: content, S3 mark, allocation (KiB: source/restored/bound; metadata the restored file has beyond the source's; source holes B-aligned), extents (source/restored)"
    bad=""
    for f in $files; do
        ms=$(cat "$OUT/map.src.$f")
        if [ ! -f "$dst/$f" ]; then printf '  %-14s MISSING\n' "$f"; bad="$bad $f"; continue; fi
        $H map "$dst/$f" >"$OUT/map.$d.$f"; md=$(cat "$OUT/map.$d.$f")
        $H digest "$dst/$f" >"$OUT/digest.$d.$f"
        ok=1
        if cmp -s "$OUT/digest.src.$f" "$OUT/digest.$d.$f"; then c=same; else c=DIFFERS; ok=""; fi
        n=$(node $bt "$f")
        case $n in *'"unix.sparse":true'*) mk=yes ;; *) mk=no ;; esac
        ex=$(expect_mark $f)
        [ $mk = $ex ] && mkn="$mk" || { mkn="$mk (S3: $ex) WRONG"; ok=""; }
        nb=$(blobs "$n")
        B=$(field fsbsize "$md"); [ "${B:-0}" -ge 4096 ] 2>/dev/null || B=4096
        as=$(($(field blocks "$ms") * 512)) ad=$(($(field blocks "$md") * 512))
        # Metadata in st_blocks: what is beyond the data extents (rounded
        # out to B); the restored file's beyond the source's is allowed.
        set -- $(extalloc "$ms" $B); xs=${1:-} al=${2:-unknown}
        set -- $(extalloc "$md" $B); xd=${1:-}
        meta=0
        if [ -n "$xs" ] && [ -n "$xd" ]; then
            m1=$((as - xs)) m2=$((ad - xd)); [ $m1 -ge 0 ] || m1=0; [ $m2 -ge 0 ] || m2=0
            [ $m2 -le $m1 ] || meta=$((m2 - m1))
        fi
        # S3's mark of the source, not the tool's, says what to expect.
        if [ $ex = yes ] || [ -n "${BY_CONTENT:-}" ]; then
            hard=$((as + nb * B + meta))
            if [ $al = yes ]; then bound=$((as + meta)); else bound=$hard; fi
            if [ $ad -le $bound ]; then
                a=within
            elif [ $ad -le $hard ]; then
                a="OVER the source by $(((ad - as - meta) / 1024)) KiB, its holes B-aligned"; ok=""
            else
                a="OVER the source + one block per blob by $(((ad - hard) / 1024)) KiB"; ok=""
            fi
            bk=$((bound / 1024))
        else
            bk=-
            if [ "$(s3 "$md")" = no ]; then a=dense; else a="LOST (S5: dense; S3 would mark it)"; ok=""; fi
        fi
        es=$(field nextents "$ms") ed=$(field nextents "$md")
        if [ "$(field extents "$ms")" = "$(field extents "$md")" ]; then em=same; else em=differ; fi
        printf '  %-14s content %-7s marked %-3s %4s blobs  %8s/%8s/%8s +%s %-7s %-9s extents %s/%s %s\n' "$f" $c "$mkn" \
            "$nb" $((as / 1024)) $((ad / 1024)) "$bk" $((meta / 1024)) "$al" "$a" "$es" "$ed" $em
        [ -n "$ok" ] || bad="$bad $f"
    done
    [ -z "$bad" ] && echo "all as expected" || fail "$bt->$rt:$bad"
    echo "   short extent maps that differ from the source's (not a check):"
    for f in $files; do
        [ -f "$OUT/map.$d.$f" ] || continue
        xs=$(field extents "$(cat "$OUT/map.src.$f")") xd=$(field extents "$(cat "$OUT/map.$d.$f")")
        [ "$xs" != "$xd" ] || continue
        [ ${#xs} -le 160 ] && [ ${#xd} -le 160 ] || { echo "     $f: (long; $OUT/map.*.$f)"; continue; }
        echo "     $f: source   ${xs:-(no data)}"; echo "     $f: restored ${xd:-(no data)}"
    done
done

# The old binaries' restores of the same snapshots (the latest is still the
# new binary's), for before vs after: st_blocks and times, not a check.
for t in restic rustic; do
    if [ $t = restic ]; then ob=$OLD_RESTIC; else ob=$OLD_RUSTIC; fi
    [ -n "$ob" ] && [ -f "$OUT/nodes.$t" ] || continue
    dst=$MP/dst-$t-old
    echo "== restore $t->$t with the old $t (not a check)"
    restore restore.$t-old $ob $W/repo-$t $dst; s=$?
    $H run "$OUT/io.sync.$t-old" $syncmd
    settle
    echo "exit $s (wall $(field wall "$(cat "$OUT/io.restore.$t-old" 2>/dev/null)") s, the sync after it $(field wall "$(cat "$OUT/io.sync.$t-old" 2>/dev/null)") s)"
    echo "   KiB allocated: source / new $t->$t / old $t->$t; contents of the old restore"
    for f in $files; do
        [ -f "$dst/$f" ] || { printf '  %-14s MISSING\n' "$f"; continue; }
        $H map "$dst/$f" >"$OUT/map.$t-old.$f"
        $H digest "$dst/$f" >"$OUT/digest.$t-old.$f"
        cmp -s "$OUT/digest.src.$f" "$OUT/digest.$t-old.$f" && c=same || c=DIFFERS
        nw=-; [ -f "$OUT/map.$t-$t.$f" ] && nw=$(($(field blocks "$(cat "$OUT/map.$t-$t.$f")") / 2))
        printf '  %-14s %9s %9s %9s  %s\n' "$f" $(($(field blocks "$(cat "$OUT/map.src.$f")") / 2)) "$nw" \
            $(($(field blocks "$(cat "$OUT/map.$t-old.$f")") / 2)) $c
    done
    rm -rf "$dst"
done

# The big file alone, read again (--force), measured; and the old
# binaries: the snapshot identity, and their own measurements.
for t in restic rustic; do
    if [ $t = restic ]; then b=$RESTIC ob=$OLD_RESTIC; else b=$RUSTIC ob=$OLD_RUSTIC; fi
    [ -d $W/repo-$t ] || continue
    if [ -f "$SRC/big" ]; then
        echo "== backup of the big file alone with $t (read again: --force)"
        evict_src
        backup big.$t $b $W/repo-$t --force "$SRC/big"; echo "exit $?"
    fi
    # The new rustic against the new restic, in one repository (one chunker
    # polynomial): rustic_core 9f88530 made rustic's chunker cut where
    # restic's does, so their content blob IDs are the same; restic's own
    # are checked against its old build below.
    if [ $t = rustic ] && [ -f "$OUT/nodes.rustic" ]; then
        t3="snapshot identity, rustic vs restic in one repository"
        echo "== backup of the tree with restic into rustic's repository (--force)"
        evict_src
        backup tree.restic-in-rustic $RESTIC $W/repo-rustic --force "$SRC"; s=$?
        echo "exit $s"; [ $s -eq 0 ] || fail "backup with restic into rustic's repository"
        echo "== $t3: every file's content blob IDs (SPEC3 2)"
        if ! RT_CONTENT=1 rt_nodes "$RESTIC" $W/repo-rustic "$SRC" >"$OUT/nodes.restic-in-rustic"; then
            echo "can't decode the snapshot"; fail "$t3"
        else
            bad=""
            for f in $files; do
                cn=$(node rustic "$f" | tr '\t' '\n' | grep '^content=')
                cr=$(node restic-in-rustic "$f" | tr '\t' '\n' | grep '^content=')
                if [ "$cn" = "$cr" ]; then
                    printf '  %-14s same (%s blobs)\n' "$f" "$(blobs "$cn")"
                else
                    printf '  %-14s DIFFERS: rustic %s blobs, restic %s\n' "$f" "$(blobs "$cn")" "$(blobs "$cr")"; bad="$bad $f"
                fi
            done
            [ -z "$bad" ] || fail "$t3:$bad"
        fi
    fi
    [ -n "$ob" ] || continue
    echo "== backup of the tree with the old $t into the same repository (--force)"
    evict_src
    backup tree.$t-old $ob $W/repo-$t --force "$SRC"; s=$?
    echo "exit $s"; [ $s -eq 0 ] || fail "backup with the old $t"
    sockwarn "backup with the old $t"
    t2="snapshot identity, old $t vs new"
    echo "== $t2: every file's content blob IDs (SPEC3 2)"
    if ! RT_CONTENT=1 rt_nodes "$RESTIC" $W/repo-$t "$SRC" >"$OUT/nodes.$t-old"; then
        echo "can't decode the snapshot"; fail "$t2"
    else
        bad=""
        for f in $files; do
            cn=$(node $t "$f" | tr '\t' '\n' | grep '^content=')
            co=$(node $t-old "$f" | tr '\t' '\n' | grep '^content=')
            if [ "$cn" = "$co" ]; then
                printf '  %-14s same (%s blobs)\n' "$f" "$(blobs "$cn")"
            elif [ $t = rustic ] && [ -f "$OUT/nodes.restic-in-rustic" ] &&
                [ "$cn" = "$(node restic-in-rustic "$f" | tr '\t' '\n' | grep '^content=')" ]; then
                # An older rustic cut a chunk whose 64 bytes before its
                # minimum size end in a byte other than zero earlier than
                # restic (rustic_core BACKOTTER.md, 9f88530): the new one's
                # are restic's, as above.
                printf '  %-14s differs: new %s blobs, old %s; the new are restic'"'"'s (rustic_core 9f88530'"'"'s chunker fix), not a failure\n' \
                    "$f" "$(blobs "$cn")" "$(blobs "$co")"
            else
                printf '  %-14s DIFFERS: new %s blobs, old %s\n' "$f" "$(blobs "$cn")" "$(blobs "$co")"; bad="$bad $f"
            fi
        done
        [ -z "$bad" ] || fail "$t2:$bad"
    fi
    if [ -f "$SRC/big" ]; then
        echo "== backup of the big file alone with the old $t (--force)"
        evict_src
        backup big.$t-old $ob $W/repo-$t --force "$SRC/big"; echo "exit $?"
    fi
done

echo "== bytes read and time of each backup (not a check; rchar: through read(), read_bytes: from the disk)"
[ "$FS" = dir ] && echo "   (in a directory: what was read before may still be cached, the ARC on ZFS; rchar is the figure)"
for l in tree.restic tree.rustic big.restic big.rustic tree.restic-in-rustic tree.restic-old tree.rustic-old big.restic-old big.rustic-old; do
    [ -f "$OUT/io.$l" ] && showio $l
done
echo "== time of each restore and of the sync after it (not a check)"
for d in restic-restic rustic-rustic restic-rustic rustic-restic restic-old rustic-old; do
    for l in restore.$d sync.$d; do [ -f "$OUT/io.$l" ] && showio $l; done
done
echo "== maps, digests, decoded snapshots, measurements: $OUT"
echo "sparse-rt: $([ -z "$failed" ] && echo PASS || echo "FAIL: ${failed#; }")"
