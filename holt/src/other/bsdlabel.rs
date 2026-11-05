//! A BSD disklabel as NetBSD's and OpenBSD's disklabel(8) prints it (and
//! takes it back with -R), and the mount(8) output of those systems.

/// One disklabel partition: its letter, sectors, offset (from the disk's
/// start), and fstype as disklabel names it.
#[derive(Debug, Clone, PartialEq)]
pub struct LabelPart {
    pub letter: char,
    pub size: u64,
    pub offset: u64,
    pub fstype: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Label {
    pub bytes_per_sector: u64,
    pub total_sectors: u64,
    /// OpenBSD's disklabel UID.
    pub duid: Option<String>,
    pub parts: Vec<LabelPart>,
}

/// `disklabel DISK`'s text.  Sizes and offsets are in sectors (OpenBSD may
/// print them with a unit when asked; this reads the plain form).
pub fn parse(text: &str) -> Label {
    let mut l = Label::default();
    let mut in_parts = false;
    for line in text.lines() {
        let t = line.trim();
        if let Some(v) = t.strip_prefix("bytes/sector:") {
            l.bytes_per_sector = v.trim().parse().unwrap_or(512);
        } else if let Some(v) = t.strip_prefix("total sectors:") {
            l.total_sectors = v.trim().parse().unwrap_or(0);
        } else if let Some(v) = t.strip_prefix("duid:") {
            let v = v.trim();
            if !v.is_empty() && v.bytes().any(|c| c != b'0') {
                l.duid = Some(v.to_string());
            }
        } else if t.ends_with("partitions:") {
            in_parts = true;
        } else if in_parts {
            let Some((letter, rest)) = t.split_once(':') else { continue };
            let mut chars = letter.chars();
            let (Some(c), None) = (chars.next(), chars.next()) else { continue };
            if !c.is_ascii_lowercase() {
                continue;
            }
            let f: Vec<&str> = rest.split_whitespace().collect();
            if f.len() < 3 {
                continue;
            }
            let (Ok(size), Ok(offset)) = (f[0].parse::<u64>(), f[1].parse::<u64>()) else { continue };
            l.parts.push(LabelPart { letter: c, size, offset, fstype: f[2].to_string() });
        }
    }
    l
}

/// A mount(8) line of NetBSD or OpenBSD: "SPEC on MP type FS (opts)",
/// OpenBSD's SPEC followed by "(DUID.x)".  (spec, mount point, type,
/// options).
pub fn parse_mount_line(l: &str) -> Option<(String, String, String, String)> {
    let (spec, rest) = l.split_once(" on ")?;
    let spec = spec.split(" (").next()?.trim().to_string();
    let (mp, rest) = rest.rsplit_once(" type ")?;
    let (fs, opts) = match rest.split_once(" (") {
        Some((fs, o)) => (fs.trim(), o.trim_end_matches(')')),
        None => (rest.trim(), ""),
    };
    Some((spec, mp.to_string(), fs.to_string(), opts.split(", ").collect::<Vec<_>>().join(",")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn openbsd_label() {
        let t = "# /dev/rsd0c:\ntype: SCSI\nduid: 1a4a1728e6a303a2\nbytes/sector: 512\ntotal sectors: 33554432\nboundstart: 64\n\n16 partitions:\n#                size           offset  fstype [fsize bsize   cpg]\n  a:         29360064               64  4.2BSD   2048 16384 12960 # /\n  b:          4194304         29360128    swap                    # none\n  c:         33554432                0  unused                    \n";
        let l = parse(t);
        assert_eq!(l.duid.as_deref(), Some("1a4a1728e6a303a2"));
        assert_eq!(l.total_sectors, 33554432);
        assert_eq!(l.parts.len(), 3);
        assert_eq!(l.parts[0], LabelPart { letter: 'a', size: 29360064, offset: 64, fstype: "4.2BSD".into() });
        assert_eq!(l.parts[1].fstype, "swap");
    }

    #[test]
    fn mount_lines() {
        assert_eq!(
            parse_mount_line("/dev/sd0a (1a4a1728e6a303a2.a) on / type ffs (rw, local, wxallowed, ctime=Thu Nov  5 21:11:54 2026)").unwrap(),
            ("/dev/sd0a".into(), "/".into(), "ffs".into(), "rw,local,wxallowed,ctime=Thu Nov  5 21:11:54 2026".into())
        );
        assert_eq!(parse_mount_line("/dev/ld0a on / type ffs (log, local)").unwrap().3, "log,local");
        assert_eq!(parse_mount_line("tmpfs on /var/shm type tmpfs (local)").unwrap().1, "/var/shm");
    }
}
