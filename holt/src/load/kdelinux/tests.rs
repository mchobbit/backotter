use super::*;
use crate::testutil::Tree;

const V1: &str = "202611240254";
const V2: &str = "202611270255";

fn run(script: &str) -> (bool, String, String) {
    let o = std::process::Command::new("sh")
        .arg("-c")
        .arg(script)
        .output()
        .unwrap();
    (
        o.status.success(),
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

fn want() -> Want {
    Want {
        images: vec![
            (V1.into(), format!("kde-linux_{V1}.erofs"), Some(4)),
            (V2.into(), format!("kde-linux_{V2}.erofs"), Some(8)),
        ],
        booted: V2.into(),
        halves: vec![],
        mstacks: vec![
            (format!("etc_{V1}.mstack"), true),
            (format!("etc_{V2}.mstack"), true),
        ],
        etc_snapshots: vec![".etc.1763990000".into()],
        layer0: Some(LAYER0.into()),
    }
}

/// A restored tree: two versions, V1's UKI still `+3`, V2 blessed.
fn tree() -> Tree {
    let t = Tree::new("kdelinux-check");
    t.file(&format!("/kde-linux_{V1}.erofs"), "1111")
        .file(&format!("/kde-linux_{V2}.erofs"), "22222222")
        .dir(&format!("/etc.mstack.v/etc_{V1}.mstack/rw/data"))
        .dir(&format!("/etc.mstack.v/etc_{V2}.mstack/rw/data"))
        .symlink(&format!("/etc.mstack.v/etc_{V2}.mstack/layer@0"), LAYER0)
        .dir("/@system/.etc.1763990000")
        .file(&format!("/@system/boot/EFI/Linux/kde-linux_{V1}+3.efi"), "u")
        .file(&format!("/@system/boot/EFI/Linux/kde-linux_{V2}.efi"), "u")
        .file("/@system/boot/EFI/BOOT/BOOTX64.EFI", "b")
        .file("/@system/boot/EFI/systemd/systemd-bootx64.efi", "b");
    t
}

/// The script, with the `.etc.<epoch>` directory's own inode standing in
/// for a subvolume root's 256 (a test can't make a subvolume).
fn script(t: &Tree, w: &Want) -> String {
    use std::os::unix::fs::MetadataExt;
    let ino = std::fs::metadata(t.p("/@system/.etc.1763990000")).unwrap().ino();
    check_script(&t.p("/").to_string_lossy(), w).replace("\nsv=256\n", &format!("\nsv={ino}\n"))
}

#[test]
fn check_passes_on_a_whole_tree() {
    let t = tree();
    let (ok, out, err) = run(&script(&t, &want()));
    assert!(ok, "{out}{err}");
    assert!(out.contains(&format!("2 UKIs, each with its image file; the booted version {V2}")), "{out}");
    // The real script asks for inode 256.
    assert!(check_script("/t", &want()).contains("\nsv=256\n"));
}

#[test]
fn check_fails_on_each_mismatch() {
    let cases: Vec<(&str, Box<dyn Fn(&Tree)>, Box<dyn Fn(&mut Want)>)> = vec![
        (
            "kde-linux_202611240254.erofs is 3 bytes, the dump's 4",
            Box::new(|t: &Tree| {
                t.file(&format!("/kde-linux_{V1}.erofs"), "111");
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "kde-linux_202611240254+3.efi: kde-linux_202611240254.erofs isn't at the restored top level",
            Box::new(|t: &Tree| {
                std::fs::remove_file(t.p(&format!("/kde-linux_{V1}.erofs"))).unwrap();
            }),
            Box::new(|w: &mut Want| {
                w.images.remove(0);
            }),
        ),
        (
            "the booted version 202611270255 has no UKI on the restored ESP",
            Box::new(|t: &Tree| {
                std::fs::remove_file(t.p(&format!("/@system/boot/EFI/Linux/kde-linux_{V2}.efi"))).unwrap();
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "etc.mstack.v/etc_202611270255.mstack has no rw/data",
            Box::new(|t: &Tree| {
                std::fs::remove_dir_all(t.p(&format!("/etc.mstack.v/etc_{V2}.mstack/rw"))).unwrap();
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "@system/.etc.1763990000 isn't restored",
            Box::new(|t: &Tree| {
                std::fs::remove_dir(t.p("/@system/.etc.1763990000")).unwrap();
                t.dir("/@system/.etc.other");
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "layer@0 is /elsewhere, not /sysusr/usr/share/factory/etc",
            Box::new(|t: &Tree| {
                let l = t.p(&format!("/etc.mstack.v/etc_{V2}.mstack/layer@0"));
                std::fs::remove_file(&l).unwrap();
                std::os::unix::fs::symlink("/elsewhere", &l).unwrap();
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "EFI/systemd/systemd-bootx64.efi isn't on the restored ESP",
            Box::new(|t: &Tree| {
                std::fs::remove_file(t.p("/@system/boot/EFI/systemd/systemd-bootx64.efi")).unwrap();
            }),
            Box::new(|_: &mut Want| {}),
        ),
        (
            "version 202611240254's image is here, but the dump hadn't it",
            Box::new(|_: &Tree| {}),
            Box::new(|w: &mut Want| {
                w.images.remove(0);
            }),
        ),
    ];
    for (msg, change, wchange) in cases {
        let t = tree();
        change(&t);
        let mut w = want();
        wchange(&mut w);
        // A removed .etc snapshot: the inode stand-in is .etc.other's.
        let mut sc = check_script(&t.p("/").to_string_lossy(), &w);
        if let Ok(m) = std::fs::metadata(t.p("/@system/.etc.1763990000")) {
            use std::os::unix::fs::MetadataExt;
            sc = sc.replace("\nsv=256\n", &format!("\nsv={}\n", m.ino()));
        }
        let (ok, out, err) = run(&sc);
        assert!(!ok, "{msg}: passed: {out}");
        assert!(err.contains(msg), "{msg}: {err}");
    }
}

/// A directory where a subvolume should be fails: its inode isn't 256.
#[test]
fn check_fails_on_a_plain_directory_for_a_subvolume() {
    let t = tree();
    let (ok, _, err) = run(&check_script(&t.p("/").to_string_lossy(), &want()));
    assert!(!ok);
    assert!(err.contains("@system/.etc.1763990000 isn't a subvolume"), "{err}");
}

/// A half version the dump had (not the booted one): a warning, kept.
#[test]
fn check_warns_on_a_known_half_version() {
    let t = tree();
    std::fs::remove_file(t.p(&format!("/kde-linux_{V1}.erofs"))).unwrap();
    let mut w = want();
    w.images.remove(0);
    w.halves = vec![V1.into()];
    let (ok, out, err) = run(&script(&t, &w));
    assert!(ok, "{out}{err}");
    assert!(err.contains(&format!("kdelinux: warning: kde-linux_{V1}+3.efi: version {V1} has no kde-linux_{V1}.erofs")), "{err}");
}

#[test]
fn uki_versions() {
    assert_eq!(uki_version(&format!("EFI/Linux/kde-linux_{V1}+3.efi")).as_deref(), Some(V1));
    assert_eq!(uki_version(&format!("kde-linux_{V2}+2-0.efi")).as_deref(), Some(V2));
    assert_eq!(uki_version(&format!("kde-linux_{V2}+0-3.efi")).as_deref(), Some(V2));
    assert_eq!(uki_version(&format!("kde-linux_{V2}.efi")).as_deref(), Some(V2));
    assert_eq!(uki_version("kde-linux_.efi"), None);
    assert_eq!(uki_version("other_1.efi"), None);
    assert_eq!(uki_version(&format!("kde-linux_{V2}.conf")), None);
}
