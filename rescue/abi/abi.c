/*
 * The header test's C side.  It prints the same lines as `bko-rescue --abi`
 * (src/term/ffi.rs's abi_lines), from this system's own headers, one fact a
 * line.  abi/check.sh builds it, compares the two, and compares the symbols
 * each side imports.
 *
 * Built with -DBKO_IOCTL_REQ=<our ioctl request type>: the redeclaration
 * below is a conflicting type, and so a failed build, if ours is wrong.
 * And with -DBKO_STATVFS_BUF=<bytes>, ffi.rs's statvfs buffer: the "room"
 * line says whether this system's struct statvfs fits in it.
 */
#include <stddef.h>
#include <stdio.h>
#include <poll.h>
#include <signal.h>
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/statvfs.h>
#if defined(__sun)
#include <stropts.h>
#endif
#if defined(__OpenBSD__)
#include <sys/types.h>
#include <dev/wscons/wsconsio.h>
#endif

#ifndef BKO_IOCTL_REQ
#error "build with -DBKO_IOCTL_REQ=<type>, as abi/check.sh does"
#endif
/* ffi.rs's statvfs buffer, from --abi's "room statvfs" line. */
#ifndef BKO_STATVFS_BUF
#error "build with -DBKO_STATVFS_BUF=<bytes>, as abi/check.sh does"
#endif
/* The brackets keep a function-like ioctl macro (Haiku's) from expanding. */
int (ioctl)(int, BKO_IOCTL_REQ, ...);
#if defined(__HAIKU__)
/* What ffi.rs declares and calls on Haiku, where ioctl is a macro over it. */
int __ioctl(int, BKO_IOCTL_REQ, void *, size_t);
#endif

#define XSTR(x) STR(x)
#define STR(x) #x
#define SIZE(name, t) printf("size %s %lu\n", name, (unsigned long)sizeof(t))
#define OFF(name, t, f) printf("offset %s.%s %lu\n", name, #f, (unsigned long)offsetof(t, f))
#define CONST(c) printf("const %s 0x%lx\n", #c, (unsigned long)(c))

int main(int argc, char **argv)
{
	(void)argv;
	SIZE("pointer", void *);
	SIZE("tcflag_t", tcflag_t);
	SIZE("cc_t", cc_t);
	SIZE("speed_t", speed_t);
	SIZE("termios", struct termios);
	OFF("termios", struct termios, c_iflag);
	OFF("termios", struct termios, c_oflag);
	OFF("termios", struct termios, c_cflag);
	OFF("termios", struct termios, c_lflag);
#if defined(__linux__) || defined(__HAIKU__)
	OFF("termios", struct termios, c_line);
#endif
	OFF("termios", struct termios, c_cc);
#if !defined(__sun)
	OFF("termios", struct termios, c_ispeed);
	OFF("termios", struct termios, c_ospeed);
#endif
#if defined(__HAIKU__)
	OFF("termios", struct termios, c_ispeed_high);
	OFF("termios", struct termios, c_ospeed_high);
#endif
	CONST(NCCS);
	CONST(TCSANOW);
	CONST(TCSADRAIN);
	CONST(BRKINT);
	CONST(ICRNL);
	CONST(INLCR);
	CONST(IGNCR);
	CONST(ISTRIP);
	CONST(INPCK);
	CONST(IXON);
	CONST(ICANON);
	CONST(ECHO);
	CONST(ECHONL);
	CONST(ISIG);
	CONST(IEXTEN);
#if defined(__FreeBSD__) || defined(__NetBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
	CONST(PENDIN);
#endif
	CONST(VMIN);
	CONST(VTIME);
	CONST(TIOCGWINSZ);
#if defined(__OpenBSD__)
	CONST(WSDISPLAYIO_GTYPE);
	CONST(WSDISPLAY_TYPE_ISAVGA);
	CONST(WSDISPLAY_TYPE_PCIVGA);
#endif
	printf("type ioctl.request %s\n", XSTR(BKO_IOCTL_REQ));
	SIZE("winsize", struct winsize);
	OFF("winsize", struct winsize, ws_row);
	OFF("winsize", struct winsize, ws_col);
	OFF("winsize", struct winsize, ws_xpixel);
	OFF("winsize", struct winsize, ws_ypixel);
	SIZE("nfds_t", nfds_t);
	SIZE("pollfd", struct pollfd);
	OFF("pollfd", struct pollfd, fd);
	OFF("pollfd", struct pollfd, events);
	OFF("pollfd", struct pollfd, revents);
	CONST(POLLIN);
	CONST(SIGHUP);
	CONST(SIGINT);
	CONST(SIGTERM);
	CONST(SIGKILL);
	SIZE("pid_t", pid_t);
	CONST(SIG_DFL);
	OFF("statvfs", struct statvfs, f_frsize);
	OFF("statvfs", struct statvfs, f_bavail);
	printf("size statvfs.f_frsize %lu\n", (unsigned long)sizeof(((struct statvfs *)0)->f_frsize));
	printf("size statvfs.f_bavail %lu\n", (unsigned long)sizeof(((struct statvfs *)0)->f_bavail));
	if (sizeof(struct statvfs) <= BKO_STATVFS_BUF)
		printf("room statvfs %lu\n", (unsigned long)BKO_STATVFS_BUF);
	else
		printf("room statvfs %lu is short of the struct's %lu\n", (unsigned long)BKO_STATVFS_BUF, (unsigned long)sizeof(struct statvfs));

	/* Never run: these calls make the program import each function under
	 * the name the headers give it, for check.sh's nm. */
	if (argc > 99) {
		struct termios t;
		struct winsize ws;
		struct pollfd p;
		struct statvfs sv;
		tcgetattr(0, &t);
		tcsetattr(0, TCSADRAIN, &t);
		ioctl(0, TIOCGWINSZ, &ws);
		poll(&p, 1, 0);
		signal(SIGTERM, SIG_DFL);
		raise(SIGTERM);
		kill(0, 0);
		(void)ttyname(0);
		(void)setsid();
		(void)statvfs("/", &sv);
	}
	return 0;
}
