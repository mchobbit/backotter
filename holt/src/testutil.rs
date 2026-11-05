//! Helpers for the tests (unit and integration): a temporary directory that
//! cleans itself up, and a builder for fake system trees (a root with its own
//! /sys, /proc, /dev, /etc and /run) that `Sys` reads instead of the host's.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

static COUNTER: AtomicUsize = AtomicUsize::new(0);

pub struct TempDir(PathBuf);

impl TempDir {
    pub fn new(tag: &str) -> TempDir {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let base = std::env::var_os("BKO_HOLT_TEST_TMP")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let p = base.join(format!("bko-holt-{tag}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).expect("create temp dir");
        TempDir(p)
    }
    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A fake root directory.  Paths are given absolute ("/sys/block/sda/size")
/// and land under the tree's own directory.
pub struct Tree {
    dir: TempDir,
}

impl Tree {
    pub fn new(tag: &str) -> Tree {
        Tree { dir: TempDir::new(tag) }
    }
    pub fn root(&self) -> &Path {
        self.dir.path()
    }
    pub fn p(&self, abs: &str) -> PathBuf {
        self.dir.path().join(abs.trim_start_matches('/'))
    }
    pub fn file(&self, abs: &str, content: impl AsRef<[u8]>) -> &Tree {
        let p = self.p(abs);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, content).unwrap();
        self
    }
    pub fn dir(&self, abs: &str) -> &Tree {
        std::fs::create_dir_all(self.p(abs)).unwrap();
        self
    }
    pub fn symlink(&self, abs: &str, target: &str) -> &Tree {
        let p = self.p(abs);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        let _ = std::fs::remove_file(&p);
        std::os::unix::fs::symlink(target, p).unwrap();
        self
    }

    /// /etc/os-release of Debian 13.
    pub fn debian13(&self) -> &Tree {
        self.file(
            "/etc/os-release",
            "PRETTY_NAME=\"Debian GNU/Linux 13 (trixie)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"13\"\nVERSION=\"13 (trixie)\"\nVERSION_CODENAME=trixie\nDEBIAN_VERSION_FULL=13.1\nID=debian\nHOME_URL=\"https://www.debian.org/\"\n",
        )
        .file("/etc/debian_version", "13.1\n")
    }

    /// A whole disk in /sys/block and /dev, `image` its contents.
    pub fn disk(&self, name: &str, devno: &str, image: &[u8], sector: u32) -> &Tree {
        let b = format!("/sys/block/{name}");
        self.file(&format!("{b}/dev"), format!("{devno}\n"))
            .file(&format!("{b}/size"), format!("{}\n", image.len() / 512))
            .file(&format!("{b}/queue/logical_block_size"), format!("{sector}\n"))
            .file(&format!("{b}/queue/physical_block_size"), format!("{sector}\n"))
            .file(&format!("{b}/queue/rotational"), "0\n")
            .file(&format!("{b}/removable"), "0\n")
            .file(&format!("{b}/ro"), "0\n")
            .dir(&format!("{b}/holders"))
            .dir(&format!("{b}/slaves"))
            .file(&format!("/dev/{name}"), image)
    }

    /// A partition of `disk`: sysfs entry (start and size in 512-byte units)
    /// and /dev node with the matching slice of `image`.
    #[allow(clippy::too_many_arguments)]
    pub fn partition(&self, disk: &str, name: &str, devno: &str, number: u32, start: u64, size: u64, image: &[u8]) -> &Tree {
        let b = format!("/sys/block/{disk}/{name}");
        let s = (start * 512) as usize;
        let e = ((start + size) * 512) as usize;
        let slice = image.get(s..e.min(image.len())).unwrap_or(&[]);
        self.file(&format!("{b}/dev"), format!("{devno}\n"))
            .file(&format!("{b}/partition"), format!("{number}\n"))
            .file(&format!("{b}/start"), format!("{start}\n"))
            .file(&format!("{b}/size"), format!("{size}\n"))
            .dir(&format!("{b}/holders"))
            .file(&format!("/dev/{name}"), slice)
    }
}
