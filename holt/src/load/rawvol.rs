//! Thin snapshots and raw volumes in load (docs/design/qubes.md, Gate 2 and
//! "What Q2-Q4 built").
//!
//! A thin LV with an origin is made by `lvcreate -s` in the restore stage,
//! once its origin's content is there; a raw volume (an LV whose content the
//! dump took as a sparse image, crate::qubes::raw_volumes) is written from
//! the backup's image by `bko-holt raw-write`, which writes only the chunks
//! that differ from what the LV already holds: a fresh thin LV reads zeros,
//! so a base image's zero runs stay unprovisioned, and a snapshot gets only
//! the chunks that differ from its origin, so the pool's sharing survives.

use super::commands::{lvm_argv, lvm_size, thin_of, Builder, Cmd, ThinPool};
use super::{shell_quote, RUN_DIR};
use crate::json::Value;
use std::fs::File;
use std::io::Read;
use std::os::unix::fs::FileExt;

fn s(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_string)
}

/// The device-mapper name LVM gives VG/LV (each `-` doubled).
pub fn mapper_name(vg: &str, lv: &str) -> String {
    format!("{}-{}", vg.replace('-', "--"), lv.replace('-', "--"))
}

/// The plan's word on a pool's provisioned data (Gate 2, point 4): what the
/// source used of its data and metadata (the dump's data_used_bytes and
/// metadata_used_bytes) beside what it's made with here, and its thin LVs'
/// virtual sizes added up (which can be several times the pool).  Used data
/// above the pool made here is a problem: the restore couldn't fit.
pub(crate) fn pool_use_note(vg: &str, p: &ThinPool, problems: &mut Vec<String>) -> Vec<Cmd> {
    let h = crate::report::human;
    let virt: u64 = p.thins.iter().map(|l| l.bytes).sum();
    let snaps = p.thins.iter().filter(|l| l.origin.is_some()).count();
    let used = match (p.data_used, p.meta_used) {
        (Some(d), Some(m)) => format!("the source used {} of its data and {} of its metadata", h(d), h(m)),
        (Some(d), None) => format!("the source used {} of its data", h(d)),
        _ => "the dump doesn't say how much of it the source used (no data_used_bytes: a dump from before Qubes's Q2)".to_string(),
    };
    for (what, u, have) in [("data", p.data_used, p.data_bytes), ("metadata", p.meta_used, p.meta_bytes)] {
        if let Some(u) = u.filter(|&u| u > have) {
            problems.push(format!("thin pool {vg}/{}: the source had {} of {what} in use, more than the {} it is made with here, so the restore can't fit in it", p.name, h(u), h(have)));
        }
    }
    vec![Cmd::note(format!(
        "thin pool {vg}/{}: made with {} of data and {} of metadata; {used}; its {} thin LVs ({snaps} of them snapshots) add up to {} virtual, which is not what it holds",
        p.name,
        h(p.data_bytes),
        h(p.meta_bytes),
        p.thins.len(),
        h(virt)
    ))]
}

/// How a raw volume's image is read back from the repository: the
/// archiver's `dump` of one file, to standard output.  rustic takes
/// SNAPSHOT:PATH; restic takes SNAPSHOT and PATH.
fn dump_argv(prefix: &[String], archiver: &str, snapshot: &str, path: &str) -> Vec<String> {
    let mut a = prefix.to_vec();
    let base = archiver.rsplit('/').next().unwrap_or(archiver);
    a.push("dump".into());
    if base.contains("restic") && !base.contains("rustic") {
        a.extend([snapshot.to_string(), path.to_string()]);
    } else {
        let (id, p) = match snapshot.split_once(':') {
            Some((id, sub)) if !sub.trim_end_matches('/').is_empty() => (id.to_string(), format!("{}{path}", sub.trim_end_matches('/'))),
            Some((id, _)) => (id.to_string(), path.to_string()),
            None => (snapshot.to_string(), path.to_string()),
        };
        a.push(format!("{id}:{p}"));
    }
    a
}

/// The raw-write stage's `sh -c` script: the archiver's DUMP (its argv)
/// piped to `EXE raw-write` on DEV, failing when the dump did.  Every value
/// is shell-quoted: IMAGE comes from the layout, in the failure message
/// too (RustAudit1 H7-02).
fn raw_write_script(dump: &[String], exe: &str, dev: &str, bytes: u64, chunk: u64, image: &str) -> String {
    let dump = dump.iter().map(|w| shell_quote(w)).collect::<Vec<_>>().join(" ");
    let st = format!("{RUN_DIR}/raw-dump.status");
    // IMAGE within the message's double quotes: shell_quote leaves a plain
    // one as it is, which is literal there (as is an empty one); any other
    // ends the double quotes for its single-quoted word.
    let q = shell_quote(image);
    let shown = if q == image || image.is_empty() { image.to_string() } else { format!("\"{q}\"") };
    format!(
        "rm -f {st}; udevadm settle; {{ {dump}; echo $? > {st}; }} | {} raw-write --size {bytes} --chunk {chunk} {}; w=$?; [ \"$(cat {st} 2>/dev/null)\" = 0 ] || {{ echo \"the archiver's dump of {shown} failed\" >&2; exit 1; }}; exit $w",
        shell_quote(exe),
        shell_quote(dev)
    )
}

impl Builder<'_> {
    /// The dm devices the dump took as raw volumes (raw_volume set).
    fn raw_dms(&self) -> Vec<Value> {
        self.layout.arr(&["storage", "dm_devices"]).iter().filter(|d| d.get("raw_volume").is_some_and(|r| !r.is_null())).cloned().collect()
    }

    /// Whether source device SRC is a raw volume: format leaves it alone.
    pub(crate) fn is_raw_volume(&self, src: &str) -> bool {
        self.raw_dms().iter().any(|d| s(d, "name").as_deref() == Some(src))
    }

    /// The main restore leaves the raw volumes' images out: they go to
    /// their LVs, not into the tree.
    pub(crate) fn raw_globs(&self) -> Vec<String> {
        if self.raw_dms().is_empty() {
            Vec::new()
        } else {
            vec![format!("!{}/**", crate::qubes::RAW_DIR)]
        }
    }

    /// Without --repo: what the restore stage would have made after the
    /// archiver, which a restore by hand must do itself.
    pub(crate) fn thin_by_hand_note(&self) -> Option<Cmd> {
        let raw = self.raw_dms().len();
        let snaps: usize = self.layout.arr(&["storage", "lvm", "volume_groups"]).iter().filter_map(|vg| thin_of(vg).ok()).flat_map(|t| t.pools).flat_map(|p| p.thins).filter(|l| l.origin.is_some()).count();
        (raw + snaps > 0).then(|| {
            Cmd::note(format!(
                "no --repo: the {snaps} thin snapshots and {raw} raw volumes are made only by the restore stage with --repo (lvcreate -s after each origin's content, then `bko-holt raw-write` from the backup's {}/VG/LV.img); by hand, do the same",
                crate::qubes::RAW_DIR
            ))
        })
    }

    /// After the main restore: each raw volume's image written to its LV
    /// (linear and other LVs first), then each volume group's thin LVs in
    /// forest order, each snapshot made from its origin as that origin now
    /// is, then given its own content.
    pub(crate) fn thin_restore(&mut self, prefix: &[String], out: &mut Vec<Cmd>) {
        let raw = self.raw_dms();
        let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "bko-holt".into());
        let write = |dm: &Value, dev: &str, bytes: u64, chunk: u64| -> Cmd {
            let r = dm.get("raw_volume").cloned().unwrap_or(Value::Null);
            let image = s(&r, "image").unwrap_or_default();
            let dump = dump_argv(prefix, &self.opts.archiver, &self.opts.snapshot, &image);
            let script = raw_write_script(&dump, &exe, dev, bytes, chunk, &image);
            let why = s(&r, "why").unwrap_or_default();
            Cmd::new(&["sh", "-c", &script]).why(format!("{image} written to {dev}, only the chunks that differ from what it holds ({why})"))
        };
        // Raw volumes that aren't thin LVs (the general rule: a linear LV
        // holding a partition table or nothing bko-holt can mount).
        let mut thin_names: Vec<(String, String)> = Vec::new();
        for vg in self.layout.arr(&["storage", "lvm", "volume_groups"]) {
            let vn = s(vg, "name").unwrap_or_default();
            for p in thin_of(vg).map(|t| t.pools).unwrap_or_default() {
                thin_names.extend(p.thins.iter().map(|l| (vn.clone(), l.name.clone())));
            }
        }
        if !raw.is_empty() {
            out.push(Cmd::new(&["mkdir", "-p", "-m", "700", RUN_DIR]));
        }
        for dm in &raw {
            let (vg, lv) = (dm.at(&["dm", "vg"]).and_then(Value::as_str).unwrap_or_default().to_string(), dm.at(&["dm", "lv"]).and_then(Value::as_str).unwrap_or_default().to_string());
            if thin_names.contains(&(vg.clone(), lv.clone())) {
                continue;
            }
            let bytes = dm.get("size_bytes").and_then(Value::as_u64).unwrap_or(0);
            out.push(write(dm, &format!("/dev/mapper/{}", mapper_name(&vg, &lv)), bytes, 64 << 10));
        }
        for vg in self.layout.arr(&["storage", "lvm", "volume_groups"]).to_vec() {
            let Ok(thin) = thin_of(&vg) else { continue };
            if thin.pools.iter().all(|p| p.thins.is_empty()) {
                continue;
            }
            let name = s(&vg, "name").unwrap_or_default();
            let devices: String = vg
                .get("physical_volumes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|pv| {
                    let src = s(pv, "device").unwrap_or_default();
                    self.names.dev(&src).unwrap_or_else(|| format!("<{src}>"))
                })
                .collect::<Vec<_>>()
                .join(",");
            let lvm = |argv: Vec<&str>| argv.iter().map(|w| shell_quote(w)).collect::<Vec<_>>().join(" ");
            for p in &thin.pools {
                let chunk = p.chunk_bytes.unwrap_or(64 << 10).max(4096);
                for l in &p.thins {
                    let full = format!("{name}/{}", l.name);
                    let dev = format!("/dev/mapper/{}", mapper_name(&name, &l.name));
                    let dm = self.layout.arr(&["storage", "dm_devices"]).iter().find(|d| d.at(&["dm", "vg"]).and_then(Value::as_str) == Some(name.as_str()) && d.at(&["dm", "lv"]).and_then(Value::as_str) == Some(l.name.as_str()) && d.at(&["dm", "lv_layer"]).is_none_or(Value::is_null)).cloned();
                    let is_raw = dm.as_ref().is_some_and(|d| d.get("raw_volume").is_some_and(|r| !r.is_null()));
                    if let Some(o) = &l.origin {
                        // (thin_problems says why.)
                        if !is_raw {
                            continue;
                        }
                        let k = if l.attrs.activation_skip { "y" } else { "n" };
                        let mut a: Vec<String> = ["--snapshot", &format!("{name}/{o}"), "-n", &l.name, "--setactivationskip", k].iter().map(|x| x.to_string()).collect();
                        for tag in &l.attrs.tags {
                            a.extend(["--addtag".into(), tag.clone()]);
                        }
                        let rest: Vec<&str> = a.iter().map(String::as_str).collect();
                        let exists = lvm(lvm_argv("lvs", &devices, &[&full]));
                        let mut script = format!("{exists} >/dev/null 2>&1 || {}", lvm(lvm_argv("lvcreate", &devices, &rest)));
                        let origin_bytes = p.thins.iter().find(|x| &x.name == o).map(|x| x.bytes).unwrap_or(l.bytes);
                        if l.bytes != origin_bytes {
                            let size = lvm_size(l.bytes);
                            let get = lvm(lvm_argv("lvs", &devices, &["--noheadings", "--units", "b", "--nosuffix", "-o", "lv_size", &full]));
                            script += &format!(" && {{ [ \"$({get} | tr -d ' ')\" = {} ] || {}; }}", l.bytes, lvm(lvm_argv("lvresize", &devices, &["--yes", "--fs", "ignore", "-L", &size, &full])));
                        }
                        if l.attrs.activation_skip {
                            script += &format!(" && {}", lvm(lvm_argv("lvchange", &devices, &["--activate", "y", "--ignoreactivationskip", &full])));
                        }
                        let size_note = if l.bytes != origin_bytes { format!(", resized to its own {} ({} for its origin)", crate::report::human(l.bytes), crate::report::human(origin_bytes)) } else { String::new() };
                        let leftover = if l.name.ends_with("-snap") { "; a leftover -snap (its qube was running, or Qubes stopped uncleanly, when the dump was made), made like any snapshot" } else { "" };
                        out.push(Cmd::new(&["sh", "-c", &script]).why(format!(
                            "thin snapshot {full}: lvcreate -s of {name}/{o} as its content now is, activation skip {} as on the source{size_note}{leftover}",
                            if l.attrs.activation_skip { "on (-ky; activated with -K to be written)" } else { "off (-kn)" }
                        )));
                        if l.attrs.read_only {
                            self.closers.push(Cmd::new(&lvm_argv("lvchange", &devices, &["--permission", "r", &full])).why(format!("{full}: read-only, as on the source")));
                        }
                    }
                    if let Some(dm_val) = dm.as_ref().filter(|_| is_raw) {
                        out.push(write(dm_val, &dev, l.bytes, chunk));
                    } else if l.name.ends_with("-volatile") {
                        self.notes.push(format!("{full}: Qubes's volatile volume, made empty (Qubes makes it again at each start of its qube)"));
                    }
                }
            }
        }
    }

    /// Every thin snapshot must come back from a raw image: one whose
    /// device the dump doesn't have (inactive when it was made), or that
    /// holds what the dump didn't take raw, is a problem, with or without
    /// --repo.
    pub(crate) fn thin_problems(&mut self) {
        for vg in self.layout.arr(&["storage", "lvm", "volume_groups"]).to_vec() {
            let Ok(thin) = thin_of(&vg) else { continue };
            let name = s(&vg, "name").unwrap_or_default();
            for l in thin.pools.iter().flat_map(|p| p.thins.iter()) {
                let Some(o) = &l.origin else { continue };
                let full = format!("{name}/{}", l.name);
                let dm = self.layout.arr(&["storage", "dm_devices"]).iter().find(|d| d.at(&["dm", "vg"]).and_then(Value::as_str) == Some(name.as_str()) && d.at(&["dm", "lv"]).and_then(Value::as_str) == Some(l.name.as_str()) && d.at(&["dm", "lv_layer"]).is_none_or(Value::is_null)).cloned();
                match dm {
                    None => self.problems.push(format!("thin snapshot {full} (of {o}) wasn't active when the dump was made, so the dump has no device for it and its content wasn't read; dump again (bko-holt dump activates such LVs read-only itself when it runs as root)")),
                    Some(d) if d.get("raw_volume").is_none_or(Value::is_null) => {
                        let what = d.at(&["content", "type"]).and_then(Value::as_str).unwrap_or("something").to_string();
                        self.problems.push(format!("thin snapshot {full} (of {o}) holds {what}, which the dump didn't take as a raw volume (it was mounted, or the dump is older than raw volumes): bko-holt load gives a thin snapshot its content only from a raw image"));
                    }
                    Some(_) => {}
                }
            }
        }
    }

    /// The plan's summary of a Qubes source's volumes (storage.qubes), by
    /// qube: which LVs come back as raw images, rather than a bare line per
    /// LV.
    pub(crate) fn qubes_note(&mut self) {
        let raw = self.raw_dms();
        let mut by: std::collections::BTreeMap<String, Vec<String>> = std::collections::BTreeMap::new();
        for d in &raw {
            if let Some(q) = d.at(&["raw_volume", "qube"]).and_then(Value::as_str) {
                by.entry(q.to_string()).or_default().push(d.at(&["dm", "lv"]).and_then(Value::as_str).unwrap_or_default().to_string());
            }
        }
        if by.is_empty() {
            return;
        }
        let parts: Vec<String> = by.iter().map(|(q, lvs)| format!("{q} ({})", lvs.len())).collect();
        self.notes.push(format!("Qubes: {} volumes of {} qubes come back as raw images, base first and each revision as its changed chunks: {}", by.values().map(Vec::len).sum::<usize>(), by.len(), parts.join(", ")));
    }
}

// ---- bko-holt raw-write ---------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Stats {
    pub bytes: u64,
    pub written: u64,
    pub same: u64,
}

/// Reads INPUT to its end, comparing each CHUNK with the same range of DEV
/// and writing only those that differ.  Past the input's end (an image
/// shorter than the device), the device is made zero where it isn't.  With
/// SIZE, the input must be exactly that long (a short stream is an archiver
/// that failed part way).
pub fn write_diff(input: &mut dyn Read, dev: &File, size: Option<u64>, chunk: usize) -> Result<Stats, String> {
    let chunk = chunk.max(512);
    let dev_size = {
        use std::io::{Seek, SeekFrom};
        let mut f = dev.try_clone().map_err(|e| e.to_string())?;
        f.seek(SeekFrom::End(0)).map_err(|e| e.to_string())?
    };
    if let Some(n) = size {
        if n > dev_size {
            return Err(format!("the image is {n} bytes, more than the device's {dev_size}"));
        }
    }
    let mut st = Stats::default();
    let (mut img, mut cur) = (vec![0u8; chunk], vec![0u8; chunk]);
    let mut at = 0u64;
    loop {
        // A full chunk, unless the input ends.
        let mut n = 0;
        while n < chunk {
            match input.read(&mut img[n..]) {
                Ok(0) => break,
                Ok(k) => n += k,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(format!("reading the image: {e}")),
            }
        }
        if n == 0 {
            break;
        }
        if at + n as u64 > dev_size {
            return Err(format!("the image is longer than the device ({dev_size} bytes)"));
        }
        same_or_write(dev, at, &img[..n], &mut cur[..n], &mut st)?;
        at += n as u64;
        if n < chunk {
            break;
        }
    }
    st.bytes = at;
    if let Some(want) = size {
        if at != want {
            return Err(format!("the image ended after {at} bytes, not {want}: the archiver's dump stopped short"));
        }
    }
    // The rest of the device reads zeros, as the source's did past its image.
    let zero = vec![0u8; chunk];
    while at < dev_size {
        let n = chunk.min((dev_size - at) as usize);
        same_or_write(dev, at, &zero[..n], &mut cur[..n], &mut st)?;
        at += n as u64;
    }
    dev.sync_all().map_err(|e| format!("sync: {e}"))?;
    Ok(st)
}

fn same_or_write(dev: &File, at: u64, want: &[u8], cur: &mut [u8], st: &mut Stats) -> Result<(), String> {
    dev.read_exact_at(cur, at).map_err(|e| format!("reading the device at {at}: {e}"))?;
    if cur == want {
        st.same += 1;
    } else {
        dev.write_all_at(want, at).map_err(|e| format!("writing the device at {at}: {e}"))?;
        st.written += 1;
    }
    Ok(())
}

/// `bko-holt raw-write [--size BYTES] [--chunk BYTES] DEV`: the image on
/// standard input written to DEV, only where it differs (load's restore
/// stage runs it; docs/design/qubes.md).
pub fn main(args: &[String]) -> u8 {
    let usage = "usage: bko-holt raw-write [--size BYTES] [--chunk BYTES] DEV < IMAGE";
    let (mut size, mut chunk, mut dev) = (None, 64usize << 10, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--size" => match it.next().and_then(|v| v.parse::<u64>().ok()) {
                Some(n) => size = Some(n),
                None => {
                    eprintln!("{usage}");
                    return 2;
                }
            },
            "--chunk" => match it.next().and_then(|v| v.parse::<usize>().ok()).filter(|&n| n >= 512) {
                Some(n) => chunk = n,
                None => {
                    eprintln!("{usage}");
                    return 2;
                }
            },
            d if dev.is_none() && !d.starts_with('-') => dev = Some(d.to_string()),
            _ => {
                eprintln!("{usage}");
                return 2;
            }
        }
    }
    let Some(dev) = dev else {
        eprintln!("{usage}");
        return 2;
    };
    let f = match std::fs::OpenOptions::new().read(true).write(true).open(&dev) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("bko-holt raw-write: {dev}: {e}");
            return 1;
        }
    };
    match write_diff(&mut std::io::stdin().lock(), &f, size, chunk) {
        Ok(st) => {
            eprintln!("bko-holt raw-write: {dev}: {} bytes; {} chunks written, {} already as the image", st.bytes, st.written, st.same);
            0
        }
        Err(e) => {
            eprintln!("bko-holt raw-write: {dev}: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dev(bytes: &[u8]) -> (std::path::PathBuf, File) {
        let p = std::env::temp_dir().join(format!("bko-raw-write-{}-{}", std::process::id(), rand_suffix()));
        std::fs::write(&p, bytes).unwrap();
        let f = std::fs::OpenOptions::new().read(true).write(true).open(&p).unwrap();
        (p, f)
    }

    fn rand_suffix() -> u64 {
        use std::sync::atomic::{AtomicU64, Ordering};
        static N: AtomicU64 = AtomicU64::new(0);
        N.fetch_add(1, Ordering::SeqCst)
    }

    /// Only the chunks that differ are written; a base image's zero chunks
    /// on a zeroed device are left alone; the device ends as the image.
    #[test]
    fn writes_only_what_differs() {
        let (p, f) = dev(&[0u8; 4096]);
        let mut img = vec![0u8; 4096];
        img[1024..1030].copy_from_slice(b"qubes!");
        let st = write_diff(&mut &img[..], &f, Some(4096), 512).unwrap();
        assert_eq!(st, Stats { bytes: 4096, written: 1, same: 7 });
        assert_eq!(std::fs::read(&p).unwrap(), img);
        // A snapshot of it: one more chunk differs, and one goes back to zero.
        let mut snap = img.clone();
        snap[3000] = 7;
        snap[1024..1030].fill(0);
        let st = write_diff(&mut &snap[..], &f, Some(4096), 512).unwrap();
        assert_eq!((st.written, st.same), (2, 6));
        assert_eq!(std::fs::read(&p).unwrap(), snap);
        std::fs::remove_file(p).unwrap();
    }

    /// A short image: the rest of the device is zeroed; with --size, a
    /// stream that stops short is an error.
    #[test]
    fn short_and_long_images() {
        let (p, f) = dev(&[9u8; 2048]);
        let st = write_diff(&mut &[1u8; 1000][..], &f, None, 512).unwrap();
        assert_eq!(st.bytes, 1000);
        let got = std::fs::read(&p).unwrap();
        assert!(got[..1000].iter().all(|&b| b == 1) && got[1000..].iter().all(|&b| b == 0));
        assert!(write_diff(&mut &[1u8; 1000][..], &f, Some(2048), 512).unwrap_err().contains("stopped short"));
        assert!(write_diff(&mut &[1u8; 4096][..], &f, None, 512).unwrap_err().contains("longer than the device"));
        assert!(write_diff(&mut &[1u8; 10][..], &f, Some(4096), 512).unwrap_err().contains("more than the device"));
        std::fs::remove_file(p).unwrap();
    }

    #[test]
    fn dump_commands() {
        let pre = vec!["rustic-bko".to_string(), "--repository".into(), "/r".into()];
        assert_eq!(dump_argv(&pre, "rustic-bko", "latest", "/.bko-holt/raw/vg/lv.img").last().unwrap(), "latest:/.bko-holt/raw/vg/lv.img");
        assert_eq!(dump_argv(&pre, "rustic-bko", "1a2b:/mnt/src", "/.bko-holt/raw/vg/lv.img").last().unwrap(), "1a2b:/mnt/src/.bko-holt/raw/vg/lv.img");
        let r = dump_argv(&pre, "/usr/bin/restic-bko", "latest", "/x.img");
        assert_eq!(&r[3..], ["dump", "latest", "/x.img"]);
        assert_eq!(mapper_name("qubes_dom0", "vm-work-private"), "qubes_dom0-vm--work--private");
    }

    /// RustAudit1 H7-02: the raw-write script quotes the layout's image in
    /// its failure message too.  A plain image's script is as before; a
    /// hostile one's parses (`sh -n`), and its message runs nothing and
    /// prints the image as it is.
    #[test]
    fn raw_write_script_quotes_the_image() {
        let pre = vec!["rustic-bko".to_string(), "--repository".into(), "/r".into()];
        let script = |image: &str| raw_write_script(&dump_argv(&pre, "rustic-bko", "latest", image), "/usr/bin/bko-holt", "/dev/mapper/vg-lv", 1 << 20, 65536, image);
        let plain = "/.bko-holt/raw/vg/lv.img";
        assert_eq!(
            script(plain),
            format!("rm -f {RUN_DIR}/raw-dump.status; udevadm settle; {{ rustic-bko --repository /r dump latest:{plain}; echo $? > {RUN_DIR}/raw-dump.status; }} | /usr/bin/bko-holt raw-write --size 1048576 --chunk 65536 /dev/mapper/vg-lv; w=$?; [ \"$(cat {RUN_DIR}/raw-dump.status 2>/dev/null)\" = 0 ] || {{ echo \"the archiver's dump of {plain} failed\" >&2; exit 1; }}; exit $w")
        );
        let mark = std::env::temp_dir().join(format!("bko-h7-02-{}", std::process::id()));
        let m = mark.display();
        for image in [
            format!("/.bko-holt/raw/vg/$(touch {m}).img"),
            format!("/.bko-holt/raw/vg/`touch {m}`.img"),
            format!("/.bko-holt/raw/vg/a\"; touch {m}; echo \".img"),
            format!("/.bko-holt/raw/vg/it's $HOME \\ ${{x}} !.img"),
            String::new(),
        ] {
            let sc = script(&image);
            let ok = std::process::Command::new("sh").args(["-n", "-c", &sc]).status().unwrap();
            assert!(ok.success(), "sh -n: {sc}");
            // The message alone, run: it prints the image and runs nothing.
            let (from, to) = (sc.find("echo \"the archiver's").unwrap(), sc.find(" >&2; exit 1;").unwrap());
            let out = std::process::Command::new("sh").args(["-c", &sc[from..to]]).output().unwrap();
            assert_eq!(String::from_utf8(out.stdout).unwrap(), format!("the archiver's dump of {image} failed\n"), "{sc}");
            assert!(!mark.exists(), "{sc}");
        }
    }
}
