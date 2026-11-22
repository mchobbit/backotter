//! `bko-holt umount-tree T`, run by load's close stage where the mount
//! stage mounts nothing at T itself: Ubuntu Core's tree is the four
//! partitions at snap-bootstrap's mount points under T/run/mnt
//! (load/snapcore.rs), and `umount --recursive T` there fails with "not
//! mounted".  It unmounts every mount at or under T, read from
//! /proc/self/mountinfo, each before the mount it's on, and fails on the
//! first umount that does (busy and the like).  Nothing mounted there is
//! success: close can run again.

use crate::mounts::{parse_mountinfo, Mount};
use std::collections::HashMap;

/// The mounts at or under T, in the order they come off: each before the
/// mount it's on (deeper in the mount tree first), the later of two at
/// the same depth first, so of two stacked at one path the top one.
pub fn order<'a>(mounts: &'a [Mount], t: &str) -> Vec<&'a Mount> {
    let parent: HashMap<u32, u32> = mounts.iter().map(|m| (m.id, m.parent)).collect();
    let depth = |m: &Mount| {
        let (mut d, mut id) = (0usize, m.id);
        while let Some(&p) = parent.get(&id) {
            if p == id || d > mounts.len() {
                break;
            }
            d += 1;
            id = p;
        }
        d
    };
    let under = |p: &str| p == t || p.strip_prefix(t).is_some_and(|r| r.starts_with('/'));
    let mut out: Vec<(usize, usize, &Mount)> = mounts.iter().enumerate().filter(|(_, m)| under(&m.target)).map(|(i, m)| (depth(m), i, m)).collect();
    out.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    out.into_iter().map(|(_, _, m)| m).collect()
}

/// Unmounts what's at or under T, reading the mounts (READ, mountinfo's
/// text) again before each umount (UMOUNT, of a path): one may take others
/// with it (propagation).  Gives back the paths unmounted, in order.
pub fn run(t: &str, mut read: impl FnMut() -> Result<String, String>, mut umount: impl FnMut(&str) -> Result<(), String>) -> Result<Vec<String>, String> {
    let t = t.trim_end_matches('/');
    if t.is_empty() || !t.starts_with('/') {
        return Err("umount-tree: the target is an absolute path other than /".into());
    }
    let mut done: Vec<String> = Vec::new();
    let mut last: Option<u32> = None;
    loop {
        let mounts = parse_mountinfo(&read()?);
        let Some(m) = order(&mounts, t).first().map(|m| (*m).clone()) else { return Ok(done) };
        if last == Some(m.id) {
            return Err(format!("{} is still mounted (mount {}) after umount said it wasn't", m.target, m.id));
        }
        umount(&m.target)?;
        done.push(m.target.clone());
        last = Some(m.id);
    }
}

/// `bko-holt umount-tree T`.
pub fn main(args: &[String]) -> u8 {
    let [t] = args else {
        eprintln!("usage: bko-holt umount-tree TARGET");
        return 2;
    };
    let read = || crate::bytes::read_to_string_bounded(std::path::Path::new("/proc/self/mountinfo"), 16 << 20).map_err(|e| format!("/proc/self/mountinfo: {e}"));
    let umount = |p: &str| {
        println!("umount {}", super::shell_quote(p));
        let o = std::process::Command::new("umount").arg(p).env("LC_ALL", "C").output().map_err(|e| format!("umount: {e}"))?;
        if o.status.success() {
            return Ok(());
        }
        let err = String::from_utf8_lossy(&o.stderr);
        Err(format!("umount {p} failed ({}){}", o.status, if err.trim().is_empty() { String::new() } else { format!(": {}", err.trim()) }))
    };
    match run(t, read, umount) {
        Ok(d) if d.is_empty() => {
            println!("umount-tree: nothing is mounted at or under {t}");
            0
        }
        Ok(_) => 0,
        Err(e) => {
            eprintln!("bko-holt umount-tree: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// Ubuntu Core's tree after the boot stage: nothing at T, the four
    /// partitions under T/run/mnt, and a bind under one of them.
    const UC24: &str = "\
22 1 0:21 / / rw - tmpfs tmpfs rw
40 22 252:21 / /mnt/bko-target/run/mnt/data rw - ext4 /dev/vdb5 rw
41 22 252:19 / /mnt/bko-target/run/mnt/ubuntu-boot rw - ext4 /dev/vdb3 rw
42 22 252:20 / /mnt/bko-target/run/mnt/ubuntu-save rw - ext4 /dev/vdb4 rw
43 22 252:18 / /mnt/bko-target/run/mnt/ubuntu-seed rw - vfat /dev/vdb2 rw
44 40 0:5 / /mnt/bko-target/run/mnt/data/system-data/dev rw - devtmpfs udev rw
45 22 0:30 / /mnt/bko-target2 rw - tmpfs tmpfs rw
46 22 0:31 / /mnt/other rw - tmpfs tmpfs rw
";

    /// A fake system: its mountinfo, from which each umount takes the top
    /// mount at the path (and fails where BUSY says).
    struct Fake {
        lines: RefCell<Vec<String>>,
        busy: Option<&'static str>,
        calls: RefCell<Vec<String>>,
    }

    impl Fake {
        fn new(text: &str) -> Fake {
            Fake { lines: RefCell::new(text.lines().map(str::to_string).collect()), busy: None, calls: RefCell::new(vec![]) }
        }
        fn go(&self, t: &str) -> Result<Vec<String>, String> {
            run(
                t,
                || Ok(self.lines.borrow().join("\n")),
                |p| {
                    self.calls.borrow_mut().push(p.to_string());
                    if self.busy == Some(p) {
                        return Err(format!("umount {p} failed (exit status: 32): umount: {p}: target is busy."));
                    }
                    let mut l = self.lines.borrow_mut();
                    let ms = parse_mountinfo(&l.join("\n"));
                    let Some(top) = ms.iter().rposition(|m| m.target == p) else { return Err(format!("umount: {p}: not mounted")) };
                    if ms.iter().any(|m| m.parent == ms[top].id) {
                        return Err(format!("umount: {p}: target is busy."));
                    }
                    l.remove(top);
                    Ok(())
                },
            )
        }
    }

    #[test]
    fn uc24_tree_without_a_root_mount() {
        let f = Fake::new(UC24);
        let d = f.go("/mnt/bko-target").unwrap();
        assert_eq!(d[0], "/mnt/bko-target/run/mnt/data/system-data/dev");
        assert_eq!(d.len(), 5, "{d:?}");
        assert_eq!(d[1..], ["/mnt/bko-target/run/mnt/ubuntu-seed", "/mnt/bko-target/run/mnt/ubuntu-save", "/mnt/bko-target/run/mnt/ubuntu-boot", "/mnt/bko-target/run/mnt/data"]);
        // Only T's: not /mnt/bko-target2, not the rest.
        let left = f.lines.borrow().join("\n");
        assert!(left.contains("/mnt/bko-target2") && left.contains("/mnt/other") && !left.contains("/mnt/bko-target/"), "{left}");
        // Run again (close again): nothing to do, and no umount.
        f.calls.borrow_mut().clear();
        assert!(f.go("/mnt/bko-target/").unwrap().is_empty());
        assert!(f.calls.borrow().is_empty());
    }

    #[test]
    fn root_mounted_tree_and_stacked_mounts() {
        let f = Fake::new(
            "22 1 0:21 / / rw - tmpfs tmpfs rw
50 22 253:1 / /mnt/bko-target rw - btrfs /dev/vdb3 rw
51 50 253:2 / /mnt/bko-target/boot rw - ext4 /dev/vdb1 rw
52 51 253:3 / /mnt/bko-target/boot/efi rw - vfat /dev/vdb2 rw
53 50 0:40 / /mnt/bko-target/home rw - btrfs /dev/vdb3 rw
54 53 0:41 / /mnt/bko-target/home rw - tmpfs tmpfs rw
55 50 0:42 / /mnt/bko-target/my\\040dir rw - tmpfs tmpfs rw
",
        );
        let d = f.go("/mnt/bko-target").unwrap();
        let at = |p: &str| d.iter().position(|x| x == p).unwrap();
        assert_eq!(d.len(), 6, "{d:?}");
        assert!(at("/mnt/bko-target/boot/efi") < at("/mnt/bko-target/boot"));
        assert_eq!(d.iter().filter(|x| *x == "/mnt/bko-target/home").count(), 2);
        assert!(d.contains(&"/mnt/bko-target/my dir".to_string()));
        assert_eq!(d.last().unwrap(), "/mnt/bko-target");
        assert_eq!(f.lines.borrow().len(), 1);
    }

    #[test]
    fn a_busy_mount_fails() {
        let mut f = Fake::new(UC24);
        f.busy = Some("/mnt/bko-target/run/mnt/ubuntu-save");
        let e = f.go("/mnt/bko-target").unwrap_err();
        assert!(e.contains("target is busy"), "{e}");
        // It stops there: ubuntu-boot and data stay mounted.
        assert_eq!(f.calls.borrow().last().unwrap(), "/mnt/bko-target/run/mnt/ubuntu-save");
        assert!(f.lines.borrow().join("\n").contains("/mnt/bko-target/run/mnt/data rw"));
    }

    #[test]
    fn a_umount_that_does_nothing_fails_and_slash_is_refused() {
        let e = run("/mnt/bko-target", || Ok(UC24.into()), |_| Ok(())).unwrap_err();
        assert!(e.contains("is still mounted"), "{e}");
        assert!(run("/", || Ok(UC24.into()), |_| Ok(())).is_err());
        assert!(run("mnt", || Ok(UC24.into()), |_| Ok(())).is_err());
        assert!(run("/mnt/bko-target", || Err("/proc/self/mountinfo: gone".into()), |_| Ok(())).unwrap_err().contains("gone"));
    }

    #[test]
    fn order_is_children_first() {
        let ms = parse_mountinfo(UC24);
        let o: Vec<&str> = order(&ms, "/mnt/bko-target").iter().map(|m| m.target.as_str()).collect();
        assert_eq!(o.first(), Some(&"/mnt/bko-target/run/mnt/data/system-data/dev"));
        assert_eq!(o.last(), Some(&"/mnt/bko-target/run/mnt/data"));
        assert!(!o.contains(&"/mnt/bko-target2"));
    }
}
