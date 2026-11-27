use super::*;
use crate::mounts::parse_mountinfo;
use crate::sys::FakeRunner;
use crate::testutil::Tree;
use crate::zfsbootmenu::sample;

/// KDE Linux's three transfers as its 202611270255 image ships them
/// ([seen], /usr/lib/sysupdate.d), comments and the continued
/// MatchPattern= included.
pub(crate) const KDE_ESP: &str = "# SPDX-License-Identifier: GPL-2.0-only\n\n[Transfer]\nProtectVersion=%A\nAppStream=https://autoconfig.kde.org/kde-linux/%W/org.kde.linux.metainfo.xml\n\n[Source]\nType=url-file\n# %W is the VARIANT_ID\nPath=https://kde-linux-images.s3.kde.org/%W/sysupdate/v2/\nMatchPattern=kde-linux_@v.efi\n\n[Target]\nType=regular-file\nPath=/EFI/Linux\nPathRelativeTo=boot\n# Keep matching kdeos so the old images get cleaned up.\nMatchPattern=kde-linux_@v+@l-@d.efi \\\n             kde-linux_@v+@l.efi \\\n             kde-linux_@v.efi \\\n             kdeos_@v+@l-@d.efi \\\n             kdeos_@v+@l.efi \\\n             kdeos_@v.efi\nMode=0644\nTriesLeft=2\nTriesDone=0\nInstancesMax=4\n";
pub(crate) const KDE_EROFS: &str = "[Transfer]\nProtectVersion=%A\n\n[Source]\nType=url-file\nPath=https://kde-linux-images.s3.kde.org/%W/sysupdate/v2/\nMatchPattern=kde-linux_@v_root-x86-64.erofs\n\n[Target]\nType=regular-file\nPath=/system\nMatchPattern=kde-linux_@v.erofs\nReadOnly=yes\nInstancesMax=4\n";
pub(crate) const KDE_CAIBX: &str = "[Transfer]\nProtectVersion=%A\n\n[Source]\nType=url-file\nPath=https://kde-linux-images.s3.kde.org/%W/sysupdate/v2/\nMatchPattern=kde-linux_@v_root-x86-64.caibx\n\n[Target]\nType=regular-file\nPath=/system\n# Intentionally different suffix.\nMatchPattern=kde-linux_@v.erofs.caibx\nReadOnly=yes\nInstancesMax=4\n";

pub(crate) const V1: &str = "202611240254";
pub(crate) const V2: &str = "202611270255";

pub(crate) fn kde_osrel(v: &str, variant: &str) -> String {
    format!(
        "NAME=\"KDE Linux\"\nPRETTY_NAME=\"KDE Linux\"\nID=kde-linux\nDEFAULT_HOSTNAME=\"$-????\"\nVERSION_ID={v}\nIMAGE_ID=\"kde-linux\"\nIMAGE_VERSION=\"{v}\"\nVARIANT=\"testing\"\nVARIANT_ID=\"{variant}\"\nKDE_LINUX_SYSUPDATE_ENDPOINT=sysupdate/v2\n"
    )
}

/// A PE file with the sections given, in order (systemd-stub's layout).
pub(crate) fn pe(secs: &[(&str, Vec<u8>)]) -> Vec<u8> {
    let pe = 0x80usize;
    let opt = 240usize;
    let table = pe + 24 + opt;
    let mut at = (table + 40 * secs.len()).div_ceil(0x200) * 0x200;
    let mut img = vec![0u8; at];
    img[0..2].copy_from_slice(b"MZ");
    img[0x3c..0x40].copy_from_slice(&(pe as u32).to_le_bytes());
    img[pe..pe + 4].copy_from_slice(b"PE\0\0");
    img[pe + 4..pe + 6].copy_from_slice(&0x8664u16.to_le_bytes());
    img[pe + 6..pe + 8].copy_from_slice(&(secs.len() as u16).to_le_bytes());
    img[pe + 20..pe + 22].copy_from_slice(&(opt as u16).to_le_bytes());
    for (i, (name, data)) in secs.iter().enumerate() {
        let e = table + 40 * i;
        img[e..e + name.len()].copy_from_slice(name.as_bytes());
        img[e + 8..e + 12].copy_from_slice(&(data.len() as u32).to_le_bytes());
        let raw = data.len().div_ceil(0x200).max(1) * 0x200;
        img[e + 16..e + 20].copy_from_slice(&(raw as u32).to_le_bytes());
        img[e + 20..e + 24].copy_from_slice(&(at as u32).to_le_bytes());
        img.resize(at + raw, 0);
        img[at..at + data.len()].copy_from_slice(data);
        at += raw;
    }
    img
}

/// A command-line addon (`ukify build --stub addonx64.efi.stub`).
pub(crate) fn addon(cmdline: &str) -> Vec<u8> {
    pe(&[
        (".sdmagic", b"#### LoaderInfo: systemd-addon 262.1 ####\0".to_vec()),
        (".cmdline", cmdline.as_bytes().to_vec()),
    ])
}

pub(crate) const KDE_CMDLINE: &str = "rw rootflags=subvol=@system,compress=zstd:-4,lazytime quiet splash plymouth.ignore-serial-consoles loglevel=3";

pub(crate) fn kde_uki(v: &str) -> Vec<u8> {
    sample::uki(&kde_osrel(v, "testing"), KDE_CMDLINE, "7.2.11-arch1-1", true)
}

/// The tree a running KDE Linux shows (RootFSv3 with etc-mstack): V1 and
/// V2, V2 booted and blessed, V1's UKI still `+3` as the installer named
/// it, an `/etc/sysupdate.d` override keeping two versions.
pub(crate) fn kde_files(t: &Tree) {
    t.file("/usr/lib/os-release", kde_osrel(V2, "testing"))
        .symlink("/etc/os-release", "../usr/lib/os-release")
        .file("/usr/lib/sysupdate.d/60-esp.transfer", KDE_ESP)
        .file("/usr/lib/sysupdate.d/50-root-x86-64-erofs.transfer", KDE_EROFS)
        .file("/usr/lib/sysupdate.d/50-root-x86-64-caibx.transfer", KDE_CAIBX)
        .file(
            "/etc/sysupdate.d/50-root-x86-64-erofs.transfer",
            KDE_EROFS.replace("InstancesMax=4", "InstancesMax=2"),
        )
        .file(&format!("/system/kde-linux_{V1}.erofs"), vec![1u8; 4096])
        .file(&format!("/system/kde-linux_{V2}.erofs"), vec![2u8; 8192])
        .file(&format!("/system/kde-linux_{V1}.erofs.caibx"), vec![3u8; 64])
        .file(&format!("/system/kde-linux_{V2}.erofs.caibx"), vec![4u8; 64])
        .file("/system/@system/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
        .dir("/system/@system/.etc.1763990000")
        .dir("/system/@system/home/tester/.snapshots/1/snapshot")
        .dir(&format!("/system/etc.mstack.v/etc_{V1}.mstack/rw/data"))
        .dir(&format!("/system/etc.mstack.v/etc_{V2}.mstack/rw/data"))
        .symlink(
            &format!("/system/etc.mstack.v/etc_{V2}.mstack/layer@0"),
            "/sysusr/usr/share/factory/etc",
        )
        .file(
            &format!("/system/etc.mstack.v/origin-for_{V2}.os-release"),
            kde_osrel(V1, "testing"),
        )
        .file(&format!("/boot/EFI/Linux/kde-linux_{V1}+3.efi"), kde_uki(V1))
        .file(&format!("/boot/EFI/Linux/kde-linux_{V2}.efi"), kde_uki(V2))
        .dir(&format!("/boot/EFI/Linux/kde-linux_{V1}.efi.extra.d"))
        .file(
            "/boot/loader/addons/bko-console.addon.efi",
            addon("console=ttyS0,115200n8"),
        )
        .file("/boot/loader/loader.conf", "timeout 7\n")
        .file("/boot/EFI/systemd/systemd-bootx64.efi", sample::uki("NAME=x\n", "", "", false))
        .file("/boot/EFI/BOOT/BOOTX64.EFI", sample::uki("NAME=x\n", "", "", false));
    use std::os::unix::fs::PermissionsExt;
    for v in [V1, V2] {
        std::fs::set_permissions(
            t.p(&format!("/system/kde-linux_{v}.erofs")),
            std::fs::Permissions::from_mode(0o444),
        )
        .unwrap();
    }
}

/// KDE Linux's mount table ([seen] the mount generator): / is @system, the
/// top level at /system, /usr the erofs on loop0, /etc the overlay, the
/// ESP at /boot behind gpt-auto's automount.
pub(crate) const KDE_MOUNTINFO: &str = "\
26 1 0:30 /@system / rw,relatime shared:1 - btrfs /dev/mapper/root rw,compress=zstd:-4,subvolid=256,subvol=/@system
27 26 0:30 / /system rw,nodev,relatime shared:2 - btrfs /dev/mapper/root rw,compress=zstd:-4,subvolid=5,subvol=/
28 26 7:0 /usr /usr ro,relatime shared:3 - erofs /dev/loop0 ro,user_xattr,acl
29 26 0:40 / /etc rw,relatime shared:4 - overlay overlay rw,lowerdir=/system/etc.mstack.v/etc_202611270255.mstack/layer@0,upperdir=/system/etc.mstack.v/etc_202611270255.mstack/rw/data,workdir=/system/etc.mstack.v/etc_202611270255.mstack/rw/work,redirect_dir=nofollow,uuid=on,metacopy=off,userxattr,index=off
30 26 0:41 / /boot rw,relatime shared:5 - autofs systemd-1 rw,fd=50,pgrp=1,timeout=120,minproto=5,maxproto=5,direct
31 30 259:1 / /boot rw,nosuid,nodev,noexec,relatime shared:6 - vfat /dev/nvme0n1p1 rw,fmask=0077,dmask=0077
";

fn kde_input_read(t: &Tree, mountinfo: &str, esp: bool) -> Option<Dump> {
    let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
    let os = crate::osrel::read(&sys);
    let mounts = parse_mountinfo(mountinfo);
    let esps: Vec<(String, String)> = if esp {
        vec![("nvme0n1p1".into(), "/boot".into())]
    } else {
        Vec::new()
    };
    let device = |m: &Mount| match m.source.as_str() {
        "/dev/mapper/root" => Some("dm-0".to_string()),
        "/dev/nvme0n1p1" => Some("nvme0n1p1".to_string()),
        _ => None,
    };
    let backing = |m: &Mount| {
        (m.source == "/dev/loop0").then(|| format!("/system/kde-linux_{V2}.erofs"))
    };
    read(
        &sys,
        &Input {
            os: os.as_ref(),
            arch: "x86_64",
            mounts: &mounts,
            esps: &esps,
            xbootldr: None,
            parts: Vec::new(),
            device: &device,
            loop_backing: &backing,
            entry_selected: Some(format!("kde-linux_{V2}.efi")),
        },
    )
}

fn st<'a>(v: &'a Value, p: &[&str]) -> Option<&'a str> {
    v.at(p).and_then(Value::as_str)
}

#[test]
fn ini_continuation_comments_and_lists() {
    let t = Transfer::parse("60-esp.transfer", "/x", KDE_ESP);
    assert_eq!(t.get("Target", "Path"), Some("/EFI/Linux"));
    assert_eq!(t.get("Target", "PathRelativeTo"), Some("boot"));
    assert_eq!(t.get("Transfer", "ProtectVersion"), Some("%A"));
    assert_eq!(
        t.target_patterns(),
        [
            "kde-linux_@v+@l-@d.efi",
            "kde-linux_@v+@l.efi",
            "kde-linux_@v.efi",
            "kdeos_@v+@l-@d.efi",
            "kdeos_@v+@l.efi",
            "kdeos_@v.efi"
        ]
    );
    assert_eq!(t.num("Target", "TriesLeft"), Some(2));
    assert_eq!(t.target_type(), "regular-file");
    // The last assignment wins; an empty one clears a list.
    let t = Transfer::parse(
        "x",
        "/x",
        "[Target]\nMatchPattern=a_@v\nMatchPattern=b_@v\nInstancesMax=4\nInstancesMax=2\n[Target]\nMatchPattern=\nMatchPattern=c_@v d_@v\n",
    );
    assert_eq!(t.target_patterns(), ["c_@v", "d_@v"]);
    assert_eq!(t.num("Target", "InstancesMax"), Some(2));
    assert_eq!(Transfer::parse("x", "/x", "").sections.len(), 0);
}

#[test]
fn patterns_match_as_sysupdate_does() {
    let pats = Transfer::parse("x", "/x", KDE_ESP).target_patterns();
    let m = match_any(&pats, &format!("kde-linux_{V2}+2-0.efi")).unwrap();
    assert_eq!(
        (m.version.as_str(), m.tries_left, m.tries_done),
        (V2, Some(2), Some(0))
    );
    let m = match_any(&pats, &format!("kde-linux_{V1}+3.efi")).unwrap();
    assert_eq!((m.version.as_str(), m.tries_left, m.tries_done), (V1, Some(3), None));
    let m = match_any(&pats, &format!("kde-linux_{V2}.efi")).unwrap();
    assert_eq!((m.version.as_str(), m.tries_left), (V2, None));
    assert_eq!(
        match_any(&pats, "kdeos_202501010000+0-3.efi").map(|m| (m.tries_left, m.tries_done)),
        Some((Some(0), Some(3)))
    );
    assert!(match_any(&pats, "kde-linux_V.efi.extra.d").is_none());
    assert!(match_any(&pats, "other.efi").is_none());
    // The image and its .caibx share the directory: each pattern takes
    // only its own.
    assert_eq!(
        match_pattern("kde-linux_@v.erofs", &format!("kde-linux_{V2}.erofs")).map(|m| m.version),
        Some(V2.to_string())
    );
    assert!(match_pattern("kde-linux_@v.erofs", &format!("kde-linux_{V2}.erofs.caibx")).is_none());
    assert!(match_pattern("kde-linux_@v.erofs.caibx", &format!("kde-linux_{V2}.erofs")).is_none());
    // A dotted version, GNOME OS's labels: the field runs to the next
    // literal's first occurrence.
    assert_eq!(
        match_pattern("gnomeos_@v.efi", "gnomeos_51.1.efi").map(|m| m.version),
        Some("51.1".into())
    );
    assert_eq!(
        match_pattern("gnomeos_usr_@v", "gnomeos_usr_51.1").map(|m| m.version),
        Some("51.1".into())
    );
    assert!(match_pattern("gnomeos_usr_@v", "_empty").is_none());
    // A field's kind is checked; a pattern without @v takes nothing; @@
    // is a literal @.
    assert!(match_pattern("x_@v+@l.efi", "x_1+a.efi").is_none());
    assert!(match_pattern("x_@v", "x_a/b").is_none());
    assert!(match_pattern("x_@v", "x_a b").is_none());
    assert!(match_pattern("fixed.efi", "fixed.efi").is_none());
    assert_eq!(
        match_pattern("a@@_@v", "a@_7").map(|m| m.version),
        Some("7".into())
    );
    assert!(match_pattern("x_@v_@h", "x_1_abc").is_none());
}

/// systemd-boot's counter in a UKI's name, `.efi` and `.conf` alike (the
/// `.efi` cases bls_counter didn't take, docs/design/kde-linux.md).
#[test]
fn counters_in_uki_names() {
    assert_eq!(
        counter("kde-linux_202611270255+3.efi"),
        ("kde-linux_202611270255.efi".into(), Some((3, 0)))
    );
    assert_eq!(
        counter("kde-linux_202611270255+2-0.efi"),
        ("kde-linux_202611270255.efi".into(), Some((2, 0)))
    );
    assert_eq!(
        counter("kde-linux_202611270255+0-3.efi"),
        ("kde-linux_202611270255.efi".into(), Some((0, 3)))
    );
    assert_eq!(counter("kde-linux_202611270255.efi"), ("kde-linux_202611270255.efi".into(), None));
    assert_eq!(counter("aeon+1-2.conf"), ("aeon.conf".into(), Some((1, 2))));
    assert_eq!(counter("v1.0+git.efi"), ("v1.0+git.efi".into(), None));
    assert_eq!(counter("+3.efi"), ("+3.efi".into(), None));
}

#[test]
fn specifiers_types_and_cmdline() {
    let os = crate::osrel::parse(&kde_osrel(V2, "testing"));
    assert_eq!(
        resolve_specifiers("%W/sysupdate/v2/ %A %M %w %% %a %q", Some(&os), "x86_64"),
        format!("testing/sysupdate/v2/ {V2} kde-linux {V2} % x86_64 %q")
    );
    assert_eq!(resolve_specifiers("%A", None, "x86_64"), "");
    assert_eq!(
        dps_type("usr", "x86_64").as_deref(),
        Some("8484680c-9521-48c6-9c11-b0720656f69e")
    );
    assert_eq!(
        dps_type("usr-verity", "x86_64").as_deref(),
        Some("77ff5f63-e7b6-4633-acf4-1565b864c0e6")
    );
    assert_eq!(
        dps_type("usr-verity-sig", "aarch64").as_deref(),
        Some("c23ce4ff-44bd-4b00-b2d4-b41b3419e02a")
    );
    assert_eq!(
        dps_type("root-x86-64", "aarch64").as_deref(),
        Some("4f68bce3-e8cd-4db1-96e7-fbcaf984b709")
    );
    assert_eq!(
        dps_type("8484680C-9521-48C6-9C11-B0720656F69E", "x86_64").as_deref(),
        Some("8484680c-9521-48c6-9c11-b0720656f69e")
    );
    assert!(dps_type("nonsense", "x86_64").is_none());
    assert_eq!(
        cmdline_arg("rw usrhash=aa quiet usrhash=bb", "usrhash").as_deref(),
        Some("bb")
    );
    assert!(cmdline_arg("rw quiet", "usrhash").is_none());
}

/// The addon reader takes a PE without `.linux`/`.initrd` (which
/// `read_uki` refuses), and a file that isn't one is listed all the same.
#[test]
fn addons_read_by_the_section_reader() {
    let t = Tree::new("sysupdate-addon");
    t.file("/esp/a.addon.efi", addon("console=ttyS0,115200n8"))
        .file("/esp/x.cred", "secret");
    let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
    assert!(read_uki(&sys, "/esp/a.addon.efi").is_none());
    let a = read_addon(&sys, "/esp/a.addon.efi", "a.addon.efi").unwrap();
    assert!(a.pe);
    assert_eq!(a.cmdline.as_deref(), Some("console=ttyS0,115200n8"));
    assert_eq!(a.stub.as_deref(), Some("systemd-addon 262.1"));
    assert_eq!(
        a.sha256.as_deref(),
        Some(crate::bytes::sha256_hex(&addon("console=ttyS0,115200n8")).as_str())
    );
    let c = read_addon(&sys, "/esp/x.cred", "x.cred").unwrap();
    assert!(!c.pe && c.cmdline.is_none());
    assert_eq!(c.size, Some(6));
    assert!(read_addon(&sys, "/esp/missing", "missing").is_none());
}

#[test]
fn kdelinux_running_system() {
    let t = Tree::new("sysupdate-kde");
    kde_files(&t);
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let j = &d.json;
    assert_eq!(st(j, &["booted_version"]), Some(V2));
    assert_eq!(st(j, &["newest_version"]), Some(V2));
    let tr = j.get("transfers").and_then(Value::as_array).unwrap();
    let files: Vec<&str> = tr.iter().filter_map(|x| st(x, &["file"])).collect();
    assert_eq!(
        files,
        [
            "50-root-x86-64-caibx.transfer",
            "50-root-x86-64-erofs.transfer",
            "60-esp.transfer"
        ]
    );
    // /etc's copy overrides the image's.
    assert_eq!(
        st(&tr[1], &["path"]),
        Some("/etc/sysupdate.d/50-root-x86-64-erofs.transfer")
    );
    assert_eq!(tr[1].at(&["target", "instances_max"]).and_then(Value::as_u64), Some(2));
    assert_eq!(tr[1].at(&["target", "read_only"]), Some(&Value::Bool(true)));
    assert_eq!(st(&tr[2], &["target", "dir"]), Some("/boot/EFI/Linux"));
    assert_eq!(st(&tr[1], &["protect_version_resolved"]), Some(V2));
    let erofs = tr[1].get("instances").and_then(Value::as_array).unwrap();
    assert_eq!(erofs.len(), 2);
    assert_eq!(st(&erofs[0], &["mode"]), Some("0444"));
    // The UKIs, with their counters and the stub's view.
    assert_eq!(d.ukis.len(), 2);
    let u1 = &d.ukis[0];
    assert_eq!(u1.name, format!("kde-linux_{V1}+3.efi"));
    assert_eq!(u1.entry_id(), format!("kde-linux_{V1}.efi"));
    assert_eq!((u1.m.tries_left, u1.m.tries_done), (Some(3), None));
    assert_eq!(
        u1.extra_d.as_ref().map(|(p, f)| (p.as_str(), f.len())),
        Some((format!("EFI/Linux/kde-linux_{V1}.efi.extra.d").as_str(), 0))
    );
    assert!(!u1.selected && d.ukis[1].selected);
    let uj = &j.get("ukis").and_then(Value::as_array).unwrap()[1];
    assert_eq!(st(uj, &["path"]), Some(format!("EFI/Linux/kde-linux_{V2}.efi").as_str()));
    assert_eq!(st(uj, &["osrel", "image_version"]), Some(V2));
    assert_eq!(st(uj, &["cmdline"]), Some(KDE_CMDLINE));
    assert_eq!(uj.get("usrhash"), Some(&Value::Null));
    assert_eq!(st(uj, &["kernel"]), Some("7.2.11-arch1-1"));
    assert_eq!(
        st(uj, &["sha256"]),
        Some(crate::bytes::sha256_hex(&kde_uki(V2)).as_str())
    );
    let ga = j.get("global_addons").and_then(Value::as_array).unwrap();
    assert_eq!(ga.len(), 1);
    assert_eq!(st(&ga[0], &["path"]), Some("loader/addons/bko-console.addon.efi"));
    assert_eq!(st(&ga[0], &["cmdline"]), Some("console=ttyS0,115200n8"));
    let vs = j.get("versions").and_then(Value::as_array).unwrap();
    assert_eq!(st(&vs[0], &["version"]), Some(V2));
    assert_eq!(vs[0].get("booted"), Some(&Value::Bool(true)));
    assert_eq!(vs[0].get("protected"), Some(&Value::Bool(true)));
    assert_eq!(vs[1].get("missing_from").and_then(Value::as_array).map(Vec::len), Some(0));
    // The KDE Linux part.
    let k = d.kdelinux.as_ref().unwrap();
    assert!(k.refusals.is_empty(), "{:?}", k.refusals);
    assert!(k.warnings.is_empty(), "{:?}", k.warnings);
    assert_eq!(st(&k.json, &["layout"]), Some("rootfsv3-etc-mstack"));
    assert_eq!(st(&k.json, &["top"]), Some("/system"));
    assert_eq!(st(&k.json, &["layer0"]), Some("/sysusr/usr/share/factory/etc"));
    let im = k.json.get("images").and_then(Value::as_array).unwrap();
    assert_eq!(im.len(), 2);
    assert_eq!(im[1].get("read_only"), Some(&Value::Bool(true)));
    assert_eq!(im[1].get("size").and_then(Value::as_u64), Some(8192));
    assert_eq!(st(&im[1], &["caibx", "file"]), Some(format!("kde-linux_{V2}.erofs.caibx").as_str()));
    assert_eq!(im[1].get("etc_mstack_rw_data"), Some(&Value::Bool(true)));
    assert_eq!(st(&im[1], &["origin"]), Some(V1));
    assert_eq!(im[0].get("origin_file"), Some(&Value::Bool(false)));
    assert_eq!(
        k.json.get("etc_snapshots"),
        Some(&Value::from(vec![".etc.1763990000".to_string()]))
    );
    // The backup tree: the top level at /, the ESP at /@system/boot.
    let src = k.sources.as_ref().unwrap();
    let pairs: Vec<(&str, Option<&str>, Option<&str>)> = src
        .iter()
        .map(|s| (s.path.as_str(), s.from.as_deref(), s.device.as_deref()))
        .collect();
    assert_eq!(
        pairs,
        [
            ("/", Some("/system"), Some("dm-0")),
            ("/@system/boot", Some("/boot"), Some("nvme0n1p1"))
        ]
    );
    assert_eq!(src[0].subvol.as_deref(), Some("/"));
    assert!(src[0].note.as_deref().unwrap().contains("btrfs top level"));
    assert_eq!(refusal(&Obj::new().set("boot", Obj::new().set("sysupdate", d.json.clone())).build()), None);
}

#[test]
fn kdelinux_half_versions() {
    let t = Tree::new("sysupdate-kde-half");
    kde_files(&t);
    // V1's image is gone (an interrupted vacuum); a V3 UKI came first (an
    // interrupted update writes the image, then the UKI: here the other way).
    std::fs::remove_file(t.p(&format!("/system/kde-linux_{V1}.erofs"))).unwrap();
    t.file("/boot/EFI/Linux/kde-linux_202611280255+2-0.efi", kde_uki("202611280255"));
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let k = d.kdelinux.as_ref().unwrap();
    let h = k.json.get("half_versions").and_then(Value::as_array).unwrap();
    let got: Vec<(&str, &str, bool)> = h
        .iter()
        .map(|x| {
            (
                st(x, &["version"]).unwrap(),
                st(x, &["has"]).unwrap(),
                x.get("booted") == Some(&Value::Bool(true)),
            )
        })
        .collect();
    assert_eq!(got, [(V1, "uki", false), ("202611280255", "uki", false)]);
    assert_eq!(k.warnings.len(), 2, "{:?}", k.warnings);
    assert!(k.warnings[0].contains("interrupted update"));
    assert!(k.refusals.is_empty());
    // The booted version's UKI missing: said so (load refuses it).
    std::fs::remove_file(t.p(&format!("/boot/EFI/Linux/kde-linux_{V2}.efi"))).unwrap();
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let k = d.kdelinux.as_ref().unwrap();
    assert!(k.warnings.iter().any(|w| w.contains(V2) && w.contains("load refuses")), "{:?}", k.warnings);
}

#[test]
fn kdelinux_refusals() {
    let refused = |t: &Tree, mi: &str, esp: bool| -> Vec<String> {
        kde_input_read(t, mi, esp).unwrap().kdelinux.unwrap().refusals
    };
    // Another edition.
    let t = Tree::new("sysupdate-kde-variant");
    kde_files(&t);
    t.file("/usr/lib/os-release", kde_osrel(V2, "testing-buildstream"));
    let r = refused(&t, KDE_MOUNTINFO, true);
    assert!(r.len() == 1 && r[0].contains("testing-buildstream"), "{r:?}");
    // An older layout (RootFSv2's per-version subvolumes, @home).
    let t = Tree::new("sysupdate-kde-v2");
    kde_files(&t);
    t.dir(&format!("/system/@kde-linux_{V1}")).dir("/system/@home");
    let r = refused(&t, KDE_MOUNTINFO, true);
    assert!(r.iter().any(|x| x.contains("RootFSv1 or v2") && x.contains("@home")), "{r:?}");
    // The debug subvolume is not an older layout.
    let t = Tree::new("sysupdate-kde-debug");
    kde_files(&t);
    t.dir(&format!("/system/@kde-linux_debug_{V2}"));
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let k = d.kdelinux.unwrap();
    assert!(k.refusals.is_empty(), "{:?}", k.refusals);
    assert_eq!(
        k.json.get("debug_subvolumes"),
        Some(&Value::from(vec![format!("@kde-linux_debug_{V2}")]))
    );
    // No etc-mstack yet.
    let t = Tree::new("sysupdate-kde-nomstack");
    kde_files(&t);
    std::fs::remove_dir_all(t.p("/system/etc.mstack.v")).unwrap();
    let r = refused(&t, KDE_MOUNTINFO, true);
    assert!(r.iter().any(|x| x.contains("no etc.mstack.v")), "{r:?}");
    // No ESP mounted.
    let t = Tree::new("sysupdate-kde-noesp");
    kde_files(&t);
    let r = refused(&t, KDE_MOUNTINFO, false);
    assert!(r.iter().any(|x| x.contains("no ESP is mounted")), "{r:?}");
    // /usr not the erofs loop mount.
    let mi = KDE_MOUNTINFO.replace(" erofs /dev/loop0", " ext4 /dev/vda3");
    let r = refused(&t, &mi, true);
    assert!(r.iter().any(|x| x.contains("/usr is not an erofs mount")), "{r:?}");
    // The top level not mounted at /system.
    let mi = KDE_MOUNTINFO.replace("27 26 0:30 / /system", "27 26 0:30 /@x /system");
    let r = refused(&t, &mi, true);
    assert!(r.iter().any(|x| x.contains("isn't the btrfs top level")), "{r:?}");
    // The marker without the ID, and the ID without the marker.
    let t = Tree::new("sysupdate-kde-id");
    kde_files(&t);
    t.file("/usr/lib/os-release", "ID=arch\nIMAGE_VERSION=202611270255\nVARIANT_ID=testing\n");
    let r = refused(&t, KDE_MOUNTINFO, true);
    assert!(r.iter().any(|x| x.contains("ID=arch")), "{r:?}");
    let t = Tree::new("sysupdate-kde-nomarker");
    kde_files(&t);
    t.file("/etc/sysupdate.d/50-root-x86-64-erofs.transfer", KDE_EROFS.replace("kde-linux_@v.erofs\n", "kde-linux_@v.img\n"));
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let v = Obj::new().set("boot", Obj::new().set("sysupdate", d.json.clone())).build();
    assert!(refusal(&v).is_some_and(|r| r.contains("MatchPattern=kde-linux_@v.erofs")), "{:?}", refusal(&v));
    // No transfers at all: nothing.
    let t = Tree::new("sysupdate-none");
    t.file("/etc/os-release", "ID=debian\nVERSION_ID=13\n");
    assert!(kde_input_read(&t, "", false).is_none());
}

/// GNOME OS (asks 1 and 2 from its session): partition targets matched by
/// type and label, sysupdate's `_empty` slots, and each UKI's (and each
/// profile's) `usrhash=`.
#[test]
fn partition_targets_and_usrhash() {
    let t = Tree::new("sysupdate-gnome");
    let usr = "[Transfer]\nProtectVersion=%A\n[Source]\nType=url-file\nPath=https://os.gnome.org/download/\nMatchPattern=gnomeos_@v.usr\n[Target]\nType=partition\nPath=auto\nMatchPartitionType=usr\nMatchPattern=gnomeos_usr_@v\nReadOnly=1\n";
    let verity = "[Source]\nType=url-file\nPath=x\nMatchPattern=gnomeos_@v.verity\n[Target]\nType=partition\nPath=auto\nMatchPartitionType=usr-verity\nMatchPattern=gnomeos_usr_v_@v\n";
    let kernel = "[Source]\nType=url-file\nPath=x\nMatchPattern=gnomeos_@v.efi\n[Target]\nType=regular-file\nPath=/EFI/Linux\nPathRelativeTo=boot\nMatchPattern=gnomeos_@v+@l-@d.efi gnomeos_@v+@l.efi gnomeos_@v.efi\nTriesLeft=3\nInstancesMax=2\n";
    let hash = "a".repeat(64);
    let safe = pe(&[
        (".sdmagic", b"#### LoaderInfo: systemd-stub 258.1 ####\0".to_vec()),
        (".osrel", b"ID=org.gnome.os\nIMAGE_VERSION=51.1\n".to_vec()),
        (".cmdline", format!("rw quiet usrhash={hash}").into_bytes()),
        (".initrd", vec![0x28, 0xb5, 0x2f, 0xfd]),
        (".linux", vec![0u8; 0x400]),
        (".profile", b"ID=safe\nTITLE=Safe mode\n".to_vec()),
        (".cmdline", format!("rw nomodeset usrhash={hash}").into_bytes()),
    ]);
    t.file("/usr/lib/os-release", "ID=org.gnome.os\nVERSION_ID=51\nIMAGE_ID=gnomeos\nIMAGE_VERSION=51.1\n")
        .file("/usr/lib/sysupdate.d/81-gnomeos-usr.transfer", usr)
        .file("/usr/lib/sysupdate.d/80-gnomeos-usr-verity.transfer", verity)
        .file("/usr/lib/sysupdate.d/90-gnomeos-kernel.transfer", kernel)
        .file("/efi/EFI/Linux/gnomeos_51.1.efi", &safe)
        .file("/efi/EFI/Linux/gnomeos_51.2+3-0.efi", sample::uki("ID=org.gnome.os\nIMAGE_VERSION=51.2\n", "rw usrhash=bb", "6.18.1", true))
        .file("/efi/EFI/Linux/gnomeos_51.1.efi.extra.d/nvidia.addon.efi", addon("nvidia-drm.modeset=1"));
    let usr_t = "8484680c-9521-48c6-9c11-b0720656f69e";
    let ver_t = "77ff5f63-e7b6-4633-acf4-1565b864c0e6";
    let part = |dev: &str, label: &str, ty: &str, ro: bool| Part {
        device: dev.into(),
        partuuid: Some(format!("0000000{}-0000-4000-8000-000000000000", &dev[dev.len() - 1..])),
        partlabel: label.into(),
        type_guid: ty.into(),
        size_bytes: Some(1 << 30),
        read_only: ro,
    };
    let parts = vec![
        part("vda1", "ESP", "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", false),
        part("vda2", "gnomeos_usr_v_51.1", ver_t, false),
        part("vda3", "gnomeos_usr_51.1", usr_t, true),
        part("vda4", "_empty", ver_t, false),
        part("vda5", "gnomeos_usr_empty", usr_t, false),
        part("vda6", "root", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709", false),
    ];
    let sys = Sys::new(t.root(), Box::new(FakeRunner::default()), true);
    let os = crate::osrel::read(&sys);
    let esps = vec![("vda1".to_string(), "/efi".to_string())];
    let none = |_: &Mount| None;
    let d = read(
        &sys,
        &Input {
            os: os.as_ref(),
            arch: "x86_64",
            mounts: &[],
            esps: &esps,
            xbootldr: None,
            parts,
            device: &none,
            loop_backing: &none,
            entry_selected: None,
        },
    )
    .unwrap();
    assert!(d.kdelinux.is_none());
    let tr = d.json.get("transfers").and_then(Value::as_array).unwrap();
    let inst = |i: usize| tr[i].get("instances").and_then(Value::as_array).unwrap().clone();
    // usr-verity: vda2 (51.1) and the empty slot vda4.
    let v = inst(0);
    let got: Vec<(&str, Option<&str>, bool)> = v
        .iter()
        .map(|x| (st(x, &["device"]).unwrap(), st(x, &["version"]), x.get("empty") == Some(&Value::Bool(true))))
        .collect();
    assert_eq!(got, [("vda2", Some("51.1"), false), ("vda4", None, true)]);
    assert_eq!(st(&tr[0], &["target", "partition_type_guid"]), Some(ver_t));
    // usr: vda3 (51.1); the installer's gnomeos_usr_empty matches as
    // version "empty", as sysupdate's pattern does (gnome-os.md: how
    // sysupdate ranks it is unconfirmed).
    let u = inst(1);
    let got: Vec<(&str, Option<&str>)> = u
        .iter()
        .map(|x| (st(x, &["device"]).unwrap(), st(x, &["version"])))
        .collect();
    assert_eq!(got, [("vda3", Some("51.1")), ("vda5", Some("empty"))]);
    assert_eq!(u[0].get("read_only"), Some(&Value::Bool(true)));
    assert_eq!(st(&u[0], &["partuuid"]), Some("00000003-0000-4000-8000-000000000000"));
    // The UKIs on /efi (PathRelativeTo=boot, no XBOOTLDR): usrhash per UKI
    // and per profile; the addon in extra.d.
    let ukis = d.json.get("ukis").and_then(Value::as_array).unwrap();
    assert_eq!(ukis.len(), 2);
    assert_eq!(st(&ukis[0], &["usrhash"]), Some(hash.as_str()));
    assert_eq!(st(&ukis[0], &["profiles", "0", "profile"]), Some("ID=safe\nTITLE=Safe mode"));
    assert_eq!(st(&ukis[0], &["profiles", "0", "cmdline"]), Some(format!("rw nomodeset usrhash={hash}").as_str()));
    assert_eq!(st(&ukis[0], &["profiles", "0", "usrhash"]), Some(hash.as_str()));
    assert_eq!(st(&ukis[0], &["extra_d", "files", "0", "cmdline"]), Some("nvidia-drm.modeset=1"));
    assert_eq!(st(&ukis[1], &["usrhash"]), Some("bb"));
    assert_eq!(ukis[1].get("tries_left").and_then(Value::as_u64), Some(3));
    // 51.2's UKI came before its usr slots: missing from both.
    let vs = d.json.get("versions").and_then(Value::as_array).unwrap();
    let v512 = vs.iter().find(|v| st(v, &["version"]) == Some("51.2")).unwrap();
    assert_eq!(v512.get("missing_from").and_then(Value::as_array).map(Vec::len), Some(2));
}

#[test]
fn add_to_boot_marks_prebuilt_and_type2() {
    let t = Tree::new("sysupdate-boot");
    kde_files(&t);
    let d = kde_input_read(&t, KDE_MOUNTINFO, true).unwrap();
    let boot = Obj::new()
        .set("loader", Obj::new().set("name", "systemd-boot"))
        .set("initramfs", Obj::new().set("generator", Value::Null))
        .build();
    let b = add_to_boot(boot, Some(&d));
    assert_eq!(st(&b, &["initramfs", "generator"]), Some("prebuilt"));
    assert_eq!(st(&b, &["initramfs", "in"]), Some("uki"));
    assert_eq!(b.at(&["loader", "type2"]), Some(&Value::Bool(true)));
    assert_eq!(st(&b, &["loader", "ukis", "1"]), Some(format!("EFI/Linux/kde-linux_{V2}.efi").as_str()));
    assert!(b.get("sysupdate").is_some());
    let plain = Obj::new().set("x", 1u64).build();
    assert_eq!(add_to_boot(plain.clone(), None), plain);
}
