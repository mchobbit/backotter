//! Helpers for the tests (unit and integration): a temporary directory that
//! cleans itself up, and a builder for fake system trees (a root with its own
//! /sys, /proc, /dev, /etc and /run) that `Sys` reads instead of the host's.

// Test-only code, public for the integration tests (so not cfg(test)): a
// failure to set up a fixture should panic the test there and then.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "test fixtures: a setup failure fails the test"
)]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

static COUNTER: AtomicUsize = AtomicUsize::new(0);

pub struct TempDir(PathBuf);

impl TempDir {
    pub fn new(tag: &str) -> Self {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let base =
            std::env::var_os("BKO_HOLT_TEST_TMP").map_or_else(std::env::temp_dir, PathBuf::from);
        let p = base.join(format!("bko-holt-{tag}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).expect("create temp dir");
        Self(p)
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

/// Writes `data` to `p`, leaving its all-zero 4 KiB blocks as holes.  The
/// scenario disk images are mostly zeros (tens of MiB each, and each
/// partition's slice again under /dev): written whole they filled the tmpfs
/// the tests run in by gigabytes.  Reads see the same bytes.
fn write_sparse(p: &Path, data: &[u8]) {
    use std::os::unix::fs::FileExt;
    const BLOCK: usize = 4096;
    if data.len() < 16 * BLOCK {
        std::fs::write(p, data).unwrap();
        return;
    }
    let f = std::fs::File::create(p).unwrap();
    let mut i = 0;
    while i < data.len() {
        let end = (i + BLOCK).min(data.len());
        if data[i..end].iter().all(|&b| b == 0) {
            i = end;
            continue;
        }
        // A run of blocks that hold data, written at once.
        let mut j = end;
        while j < data.len() {
            let e = (j + BLOCK).min(data.len());
            if data[j..e].iter().all(|&b| b == 0) {
                break;
            }
            j = e;
        }
        f.write_all_at(&data[i..j], i as u64).unwrap();
        i = j;
    }
    f.set_len(data.len() as u64).unwrap();
}

/// A fake root directory.  Paths are given absolute ("/sys/block/sda/size")
/// and land under the tree's own directory.
pub struct Tree {
    dir: TempDir,
}

impl Tree {
    pub fn new(tag: &str) -> Self {
        Self {
            dir: TempDir::new(tag),
        }
    }
    pub fn root(&self) -> &Path {
        self.dir.path()
    }
    pub fn p(&self, abs: &str) -> PathBuf {
        self.dir.path().join(abs.trim_start_matches('/'))
    }
    pub fn file(&self, abs: &str, content: impl AsRef<[u8]>) -> &Self {
        let p = self.p(abs);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        write_sparse(&p, content.as_ref());
        self
    }
    pub fn dir(&self, abs: &str) -> &Self {
        std::fs::create_dir_all(self.p(abs)).unwrap();
        self
    }
    pub fn symlink(&self, abs: &str, target: &str) -> &Self {
        let p = self.p(abs);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        let _ = std::fs::remove_file(&p);
        std::os::unix::fs::symlink(target, p).unwrap();
        self
    }

    /// /etc/os-release of Debian 13.
    pub fn debian13(&self) -> &Self {
        self.file(
            "/etc/os-release",
            "PRETTY_NAME=\"Debian GNU/Linux 13 (trixie)\"\nNAME=\"Debian GNU/Linux\"\nVERSION_ID=\"13\"\nVERSION=\"13 (trixie)\"\nVERSION_CODENAME=trixie\nDEBIAN_VERSION_FULL=13.1\nID=debian\nHOME_URL=\"https://www.debian.org/\"\n",
        )
        .file("/etc/debian_version", "13.1\n")
    }

    /// A whole disk in /sys/block and /dev, `image` its contents.
    pub fn disk(&self, name: &str, devno: &str, image: &[u8], sector: u32) -> &Self {
        let b = format!("/sys/block/{name}");
        self.file(&format!("{b}/dev"), format!("{devno}\n"))
            .file(&format!("{b}/size"), format!("{}\n", image.len() / 512))
            .file(
                &format!("{b}/queue/logical_block_size"),
                format!("{sector}\n"),
            )
            .file(
                &format!("{b}/queue/physical_block_size"),
                format!("{sector}\n"),
            )
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
    pub fn partition(
        &self,
        disk: &str,
        name: &str,
        devno: &str,
        number: u32,
        start: u64,
        size: u64,
        image: &[u8],
    ) -> &Self {
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

/// A document kept as holt/tests/fixtures/DIR/NAME.json: pretty, its
/// `generated` and `version` replaced (GENERATED, VERSION), as `holt_schema.py`
/// takes a golden; checked against the file, or written again with
/// `BKO_HOLT_JSON_GOLDEN=bless`.
pub fn golden_json(dir: &str, name: &str, v: &crate::json::Value) {
    let mut v = v.clone();
    if let crate::json::Value::Obj(kv) = &mut v {
        for (k, x) in kv.iter_mut() {
            match k.as_str() {
                "generated" => *x = crate::json::Value::from("GENERATED"),
                "version" => *x = crate::json::Value::from("VERSION"),
                _ => {}
            }
        }
    }
    let text = v.pretty() + "\n";
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(dir)
        .join(format!("{name}.json"));
    if std::env::var("BKO_HOLT_JSON_GOLDEN").as_deref() == Ok("bless") {
        std::fs::create_dir_all(path.parent().expect("a fixtures directory"))
            .expect("make the fixtures directory");
        std::fs::write(&path, &text).expect("write the golden");
        return;
    }
    let want = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e} (BKO_HOLT_JSON_GOLDEN=bless writes it)",
            path.display()
        )
    });
    assert_eq!(
        text,
        want,
        "{}: the document changed (BKO_HOLT_JSON_GOLDEN=bless writes it again)",
        path.display()
    );
}
