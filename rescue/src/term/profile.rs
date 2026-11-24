//! Which console this is, and so what it gets: plain mode or full screen,
//! the line style, colour or none, and the sequences it takes.  First
//! match wins (docs/design/rescue-tui.md, "Console profiles"):
//!
//! 1. plain mode: `--plain`, stdin or stdout not a terminal, TERM unset or
//!    `dumb`;
//! 2. overrides: `--lines`, `--colour`, or BKO_RESCUE_LINES and
//!    BKO_RESCUE_COLOUR from a launcher (each overrides only its own part);
//! 3. a pseudo-terminal on any platform but Haiku is ssh: the locale
//!    decides the lines, TERM the colour;
//! 4. the platform and the tty's name;
//! 5. anything else: ASCII, no colour.
//!
//! This is RT2's first table.  RT3's spike on every console corrects it.

use super::ffi;
use super::out::{Caps, Lines};
use super::tty::Size;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Os {
    Linux,
    FreeBsd,
    NetBsd,
    OpenBsd,
    DragonFly,
    Illumos,
    Haiku,
}

impl Os {
    pub fn this() -> Os {
        match ffi::OS {
            "linux" => Os::Linux,
            "freebsd" => Os::FreeBsd,
            "netbsd" => Os::NetBsd,
            "openbsd" => Os::OpenBsd,
            "dragonfly" => Os::DragonFly,
            "illumos" => Os::Illumos,
            _ => Os::Haiku,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Os::Linux => "linux",
            Os::FreeBsd => "freebsd",
            Os::NetBsd => "netbsd",
            Os::OpenBsd => "openbsd",
            Os::DragonFly => "dragonfly",
            Os::Illumos => "illumos",
            Os::Haiku => "haiku",
        }
    }
}

/// Everything the decision reads, gathered once, so `decide` is pure.
#[derive(Clone, Debug)]
pub struct Facts {
    pub os: Os,
    /// The tty's name without `/dev/`.
    pub tty: Option<String>,
    pub term: Option<String>,
    /// The first of LC_ALL, LC_CTYPE and LANG that is set: name and value.
    pub locale: Option<(String, String)>,
    pub stdin_tty: bool,
    pub stdout_tty: bool,
    pub plain_flag: bool,
    /// An override and where it came from (`--lines`, BKO_RESCUE_LINES).
    pub lines: Option<(Lines, String)>,
    pub colour: Option<(bool, String)>,
    /// Overrides that weren't understood, said by `--profile`.
    pub ignored: Vec<String>,
}

/// Parse `on`/`off` for `--colour` and BKO_RESCUE_COLOUR.
pub fn parse_colour(s: &str) -> Option<bool> {
    match s.to_ascii_lowercase().as_str() {
        "on" | "yes" | "1" => Some(true),
        "off" | "no" | "0" => Some(false),
        _ => None,
    }
}

impl Facts {
    /// The facts of this process: fd 0's tty, the environment, and the
    /// command line's overrides (which beat the environment's).
    pub fn gather(plain: bool, lines: Option<Lines>, colour: Option<bool>) -> Facts {
        use std::io::IsTerminal;
        let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        let mut ignored = Vec::new();
        let lines = match lines {
            Some(l) => Some((l, "--lines".to_string())),
            None => env("BKO_RESCUE_LINES").and_then(|v| match Lines::parse(&v) {
                Some(l) => Some((l, "BKO_RESCUE_LINES".to_string())),
                None => {
                    ignored.push(format!("BKO_RESCUE_LINES={v}"));
                    None
                }
            }),
        };
        let colour = match colour {
            Some(c) => Some((c, "--colour".to_string())),
            None => env("BKO_RESCUE_COLOUR").and_then(|v| match parse_colour(&v) {
                Some(c) => Some((c, "BKO_RESCUE_COLOUR".to_string())),
                None => {
                    ignored.push(format!("BKO_RESCUE_COLOUR={v}"));
                    None
                }
            }),
        };
        let locale = ["LC_ALL", "LC_CTYPE", "LANG"]
            .iter()
            .find_map(|k| env(k).map(|v| (k.to_string(), v)));
        Facts {
            os: Os::this(),
            tty: ffi::tty_name(0),
            term: std::env::var("TERM").ok(),
            locale,
            stdin_tty: std::io::stdin().is_terminal(),
            stdout_tty: std::io::stdout().is_terminal(),
            plain_flag: plain,
            lines,
            colour,
            ignored,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Profile {
    /// Why plain mode, when it is.
    pub plain: Option<String>,
    pub lines: Lines,
    pub colour: bool,
    pub caps: Caps,
    /// Which rule decided, in words.
    pub rule: String,
    pub lines_why: String,
    pub colour_why: String,
}

fn locale_utf8(f: &Facts) -> bool {
    f.locale.as_ref().is_some_and(|(_, v)| {
        let v = v.to_ascii_lowercase();
        v.contains("utf-8") || v.contains("utf8")
    })
}

/// A pseudo-terminal: `pts/N` everywhere, and the old `ttyp0`-style names
/// on NetBSD and OpenBSD (OpenBSD has only those).
fn is_pty(os: Os, tty: &str) -> bool {
    if tty.starts_with("pts/") {
        return true;
    }
    let b = tty.as_bytes();
    matches!(os, Os::NetBsd | Os::OpenBsd)
        && b.len() == 5
        && tty.starts_with("tty")
        && (b'p'..=b'z').contains(&b[3])
}

fn linux_vt(tty: &str) -> bool {
    tty.strip_prefix("tty")
        .and_then(|n| n.parse::<u32>().ok())
        .is_some_and(|n| (1..=63).contains(&n))
}

/// Rules 3 to 5: lines, colour, caps, and the rule's words.
fn table(f: &Facts) -> (Lines, bool, Caps, String) {
    let tty = f.tty.as_deref().unwrap_or("");
    let term = f.term.as_deref().unwrap_or("");
    let serial = |what: &str| {
        (Lines::Ascii, false, Caps::ALL, format!("{what}: a serial console, ASCII, no colour (D3)"))
    };
    if f.os != Os::Haiku && is_pty(f.os, tty) {
        let utf = locale_utf8(f);
        let colour = !matches!(term, "vt100" | "vt102" | "vt220");
        let what = format!(
            "{tty} is a pseudo-terminal (ssh): the locale says {}, TERM {} colour",
            if utf { "UTF-8" } else { "not UTF-8" },
            if colour { "allows" } else { "has no" }
        );
        return (if utf { Lines::Utf8 } else { Lines::Ascii }, colour, Caps::ALL, what);
    }
    let screen = |lines: Lines, caps: Caps, what: &str| (lines, true, caps, what.to_string());
    match f.os {
        Os::Linux if linux_vt(tty) => screen(Lines::Utf8, Caps::ALL, "the Linux VT"),
        Os::Linux
            if ["ttyS", "ttyAMA", "ttyUSB", "hvc"].iter().any(|p| tty.starts_with(p)) =>
        {
            serial(tty)
        }
        Os::FreeBsd if tty.starts_with("ttyv") => screen(Lines::Utf8, Caps::ALL, "FreeBSD's vt(4)"),
        Os::FreeBsd if tty.starts_with("ttyu") => serial(tty),
        Os::NetBsd if tty.starts_with("ttyE") => screen(Lines::Dec, Caps::ALL, "NetBSD's wscons"),
        Os::NetBsd if tty.starts_with("tty0") => serial(tty),
        Os::OpenBsd if tty.starts_with("ttyC") => screen(Lines::Dec, Caps::ALL, "OpenBSD's wscons"),
        Os::OpenBsd if tty.starts_with("tty0") => serial(tty),
        // syscons: joined SGR, cursor hiding and clear are RT3's to check.
        Os::DragonFly if tty.starts_with("ttyv") => {
            screen(Lines::Ascii, Caps::NONE, "DragonFly's syscons (sequences unchecked, RT3)")
        }
        Os::DragonFly if tty.starts_with("ttyd") => serial(tty),
        // The screen's console has TERM sun-color; on-tty gives a serial
        // one vt100.  Which tem mode (VGA text or framebuffer) is RT3's to
        // find a sign for: until then ASCII, with --lines utf8 to force.
        Os::Illumos if (tty == "console" || tty.starts_with("vt/")) && term.starts_with("sun") => {
            screen(Lines::Ascii, Caps::NONE, "illumos's tem (mode unknown until RT3: ASCII)")
        }
        Os::Illumos if tty == "console" || tty.starts_with("term/") => serial(tty),
        Os::Haiku if term.starts_with("xterm") => screen(Lines::Utf8, Caps::ALL, "Haiku's Terminal"),
        Os::Haiku => serial("Haiku, TERM not xterm"),
        _ => (
            Lines::Ascii,
            false,
            Caps::ALL,
            format!("an unknown console ({}): ASCII, no colour", if tty.is_empty() { "no tty" } else { tty }),
        ),
    }
}

pub fn decide(f: &Facts) -> Profile {
    let term = f.term.as_deref().unwrap_or("");
    let plain = if f.plain_flag {
        Some("--plain".to_string())
    } else if !f.stdin_tty {
        Some("standard input is not a terminal".to_string())
    } else if !f.stdout_tty {
        Some("standard output is not a terminal".to_string())
    } else if term.is_empty() {
        Some("TERM is unset".to_string())
    } else if term == "dumb" {
        Some("TERM is dumb".to_string())
    } else {
        None
    };
    let (mut lines, mut colour, caps, rule) = table(f);
    let mut lines_why = "the rule".to_string();
    let mut colour_why = "the rule".to_string();
    if let Some((l, from)) = &f.lines {
        lines = *l;
        lines_why = from.clone();
    }
    if let Some((c, from)) = &f.colour {
        colour = *c;
        colour_why = from.clone();
    }
    Profile { plain, lines, colour, caps, rule, lines_why, colour_why }
}

/// `bko-rescue --profile`: what was decided and why, one `key value` a
/// line, for checks.sh and bug reports.
pub fn describe(f: &Facts, p: &Profile, size: Size) -> Vec<String> {
    let or_none = |o: &Option<String>| o.clone().unwrap_or_else(|| "(none)".to_string());
    let mut v = vec![
        format!("os {}", f.os.name()),
        format!("tty {}", or_none(&f.tty)),
        format!("term {}", or_none(&f.term)),
        format!(
            "locale {}",
            f.locale.as_ref().map(|(k, v)| format!("{k}={v}")).unwrap_or_else(|| "(none)".into())
        ),
        format!("stdin {}", if f.stdin_tty { "terminal" } else { "not a terminal" }),
        format!("stdout {}", if f.stdout_tty { "terminal" } else { "not a terminal" }),
        if size.assumed {
            format!("size {}x{} (0x0 or unknown, taken as 80x24)", size.cols, size.rows)
        } else {
            format!("size {}x{}", size.cols, size.rows)
        },
        match &p.plain {
            Some(why) => format!("mode plain ({why})"),
            None => "mode full-screen".to_string(),
        },
        format!("rule {}", p.rule),
        format!("lines {} ({})", p.lines.name(), p.lines_why),
        format!("colour {} ({})", if p.colour { "on" } else { "off" }, p.colour_why),
        format!(
            "caps sgr={} cursor={} clear={}",
            if p.caps.joined_sgr { "joined" } else { "one-each" },
            if p.caps.hide_cursor { "hide" } else { "park" },
            if p.caps.clear { "2J" } else { "spaces" }
        ),
    ];
    for i in &f.ignored {
        v.push(format!("ignored {i} (not understood)"));
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(os: Os, tty: &str, term: &str, lang: &str) -> Facts {
        Facts {
            os,
            tty: Some(tty.to_string()),
            term: Some(term.to_string()),
            locale: (!lang.is_empty()).then(|| ("LANG".to_string(), lang.to_string())),
            stdin_tty: true,
            stdout_tty: true,
            plain_flag: false,
            lines: None,
            colour: None,
            ignored: vec![],
        }
    }

    #[test]
    fn the_first_table() {
        use Lines::*;
        let cases: &[(Os, &str, &str, &str, Lines, bool)] = &[
            (Os::Linux, "tty1", "linux", "", Utf8, true),
            (Os::Linux, "tty63", "linux", "", Utf8, true),
            (Os::Linux, "tty64", "linux", "", Ascii, false),
            (Os::Linux, "ttyS0", "vt220", "", Ascii, false),
            (Os::Linux, "ttyAMA0", "vt220", "", Ascii, false),
            (Os::Linux, "hvc0", "vt220", "", Ascii, false),
            (Os::Linux, "pts/0", "xterm-256color", "C.UTF-8", Utf8, true),
            (Os::Linux, "pts/0", "xterm", "C", Ascii, true),
            (Os::Linux, "pts/0", "vt220", "en_GB.utf8", Utf8, false),
            (Os::Linux, "console", "linux", "", Ascii, false),
            (Os::FreeBsd, "ttyv0", "xterm", "", Utf8, true),
            (Os::FreeBsd, "ttyu0", "vt100", "", Ascii, false),
            (Os::FreeBsd, "pts/1", "xterm", "", Ascii, true),
            (Os::NetBsd, "ttyE0", "wsvt25", "", Dec, true),
            (Os::NetBsd, "tty00", "vt100", "", Ascii, false),
            (Os::NetBsd, "ttyp0", "xterm", "C.UTF-8", Utf8, true),
            (Os::OpenBsd, "ttyC0", "vt220", "", Dec, true),
            (Os::OpenBsd, "tty00", "vt220", "", Ascii, false),
            (Os::OpenBsd, "ttyp3", "vt220", "", Ascii, false),
            (Os::DragonFly, "ttyv0", "cons25", "", Ascii, true),
            (Os::DragonFly, "ttyd0", "dialup", "", Ascii, false),
            (Os::Illumos, "console", "sun-color", "", Ascii, true),
            (Os::Illumos, "vt/2", "sun-color", "", Ascii, true),
            (Os::Illumos, "console", "vt100", "", Ascii, false),
            (Os::Illumos, "term/a", "vt100", "", Ascii, false),
            (Os::Illumos, "pts/2", "xterm", "en_US.UTF-8", Utf8, true),
            (Os::Haiku, "tt/p0", "xterm-256color", "", Utf8, true),
            (Os::Haiku, "tt/p1", "vt100", "", Ascii, false),
            (Os::Haiku, "pts/0", "vt100", "C.UTF-8", Ascii, false),
        ];
        for &(os, tty, term, lang, lines, colour) in cases {
            let p = decide(&facts(os, tty, term, lang));
            assert_eq!((p.lines, p.colour), (lines, colour), "{os:?} {tty} {term} {lang}: {}", p.rule);
            assert_eq!(p.plain, None);
        }
    }

    #[test]
    fn syscons_and_tem_get_the_fallbacks() {
        assert_eq!(decide(&facts(Os::DragonFly, "ttyv0", "cons25", "")).caps, Caps::NONE);
        assert_eq!(decide(&facts(Os::Illumos, "console", "sun-color", "")).caps, Caps::NONE);
        assert_eq!(decide(&facts(Os::Linux, "tty1", "linux", "")).caps, Caps::ALL);
    }

    #[test]
    fn plain_mode_first() {
        let mut f = facts(Os::Linux, "tty1", "linux", "");
        f.plain_flag = true;
        assert_eq!(decide(&f).plain.as_deref(), Some("--plain"));
        let mut f = facts(Os::Linux, "tty1", "dumb", "");
        assert_eq!(decide(&f).plain.as_deref(), Some("TERM is dumb"));
        f.term = None;
        assert_eq!(decide(&f).plain.as_deref(), Some("TERM is unset"));
        let mut f = facts(Os::Linux, "tty1", "linux", "");
        f.stdout_tty = false;
        assert!(decide(&f).plain.is_some());
    }

    #[test]
    fn overrides_each_change_their_own_part() {
        let mut f = facts(Os::OpenBsd, "tty00", "vt220", "");
        f.lines = Some((Lines::Dec, "--lines".into()));
        let p = decide(&f);
        assert_eq!((p.lines, p.colour), (Lines::Dec, false));
        assert_eq!(p.lines_why, "--lines");
        f.colour = Some((true, "BKO_RESCUE_COLOUR".into()));
        assert!(decide(&f).colour);
    }

    #[test]
    fn describe_says_the_size_was_assumed() {
        let f = facts(Os::Linux, "ttyS0", "vt220", "");
        let p = decide(&f);
        let lines = describe(&f, &p, Size { rows: 24, cols: 80, assumed: true });
        assert!(lines.iter().any(|l| l.starts_with("size 80x24 (0x0")));
        assert!(lines.iter().any(|l| l == "lines ascii (the rule)"));
        assert!(lines.iter().any(|l| l == "colour off (the rule)"));
    }
}
