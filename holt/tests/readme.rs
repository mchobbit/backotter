//! holt/README.md against the code it describes (`RustAudit1` H3-06,
//! H10-04): every system `osrel::SUPPORTED` accepts is in the README's list
//! of supported systems, which says it is that list, and every command
//! main.rs's USAGE lists is named, so a new row or command can't leave the
//! README behind.

const README: &str = include_str!("../README.md");

/// The README's list of supported systems, its runs of white space made one
/// space: from its introduction to the paragraph after it.
fn supported_list() -> String {
    let from = README
        .find("The list (`src/osrel.rs`")
        .expect("README.md introduces the list of supported systems");
    let to = README[from..]
        .find("\nHoltTest1All round-trips")
        .map(|i| from + i)
        .expect("README.md's list of supported systems ends before HoltTest1All's paragraph");
    README[from..to]
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// Whether LIST names the system LABEL ("Debian 13"): the label itself, or
/// its name followed by several versions, the label's among them ("Debian
/// 12 and 13").
fn names(list: &str, label: &str) -> bool {
    if list.contains(label) {
        return true;
    }
    let Some((name, version)) = label
        .rsplit_once(' ')
        .filter(|(_, v)| v.starts_with(|c: char| c.is_ascii_digit()))
    else {
        return false;
    };
    let head = format!("{name} ");
    list.match_indices(&head).any(|(i, _)| {
        let rest = &list[i + head.len()..];
        let end = rest.find([',', ';', '(', ')']).unwrap_or(rest.len());
        rest[..end].split(' ').any(|w| w == version)
    })
}

#[test]
fn readme_lists_every_supported_system() {
    let list = supported_list();
    let missing: Vec<&str> = bko_holt::osrel::SUPPORTED
        .iter()
        .map(|s| s.2)
        .filter(|l| !names(&list, l))
        .collect();
    assert!(
        missing.is_empty(),
        "osrel::SUPPORTED's {missing:?} aren't in holt/README.md's list of supported systems"
    );
}

/// The commands main.rs's USAGE lists ("dump", "load", ...): the word after
/// `bko-holt` on each of its synopsis lines, options left out.
fn usage_commands() -> Vec<String> {
    let main = include_str!("../src/main.rs");
    let from = main
        .find("const USAGE: &str = \"")
        .expect("main.rs has USAGE")
        + "const USAGE: &str = \"".len();
    let synopsis = &main[from..from
        + main[from..]
            .find("\n\n")
            .expect("USAGE's synopsis ends in a blank line")];
    synopsis
        .lines()
        .filter_map(|l| {
            l.trim()
                .trim_start_matches("Usage:")
                .trim()
                .strip_prefix("bko-holt ")
        })
        .filter_map(|rest| rest.split_whitespace().next())
        .filter(|c| !c.starts_with('-'))
        .map(str::to_string)
        .collect()
}

#[test]
fn readme_names_every_command() {
    let cmds = usage_commands();
    for c in ["dump", "load", "report-bug", "recovery-key-file"] {
        assert!(
            cmds.iter().any(|x| x == c),
            "main.rs's USAGE as read here lists {cmds:?}, not {c}"
        );
    }
    for c in &cmds {
        assert!(
            README.contains(&format!("`bko-holt {c}")),
            "holt/README.md doesn't name `bko-holt {c}`, which main.rs's USAGE lists"
        );
    }
}

#[test]
fn names_takes_a_label_or_its_versions() {
    let list = "Debian 12 and 13 (Proxmox VE 9 on it), Ubuntu 24.04 and 26.04, KDE neon 24.04, Ubuntu Core 24";
    for l in [
        "Debian 12",
        "Debian 13",
        "Ubuntu 24.04",
        "Ubuntu 26.04",
        "KDE neon",
        "Ubuntu Core 24",
    ] {
        assert!(names(list, l), "{l}");
    }
    for l in [
        "Debian 11",
        "Ubuntu 22.04",
        "Proxmox VE 8",
        "Fedora 44",
        "Ubuntu Core 22",
    ] {
        assert!(!names(list, l), "{l}");
    }
}
